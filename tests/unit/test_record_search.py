"""Plans run as SQL over published snapshots only: a synthetic store, no network or model."""
from pathlib import Path
import tempfile
import unittest

from knowledge_store import KnowledgeStore
import record_engine
import record_profiles
import record_search

TODAY = "2026-11-11"
RECORD_SET = record_profiles.load("ciberseguridad")["record_sets"][0]
# cve, published, status, cvss, version, cwes
VULNERABILITIES = [
    ("CVE-2021-44228", "2021-12-10T10:15:00Z", "published", 10.0, "3.1", ["CWE-502", "CWE-20"]),
    ("CVE-2021-26855", "2021-03-03T00:15:00Z", "published", 9.8, "3.1", ["CWE-918"]),
    ("CVE-2022-0847", "2022-03-10T17:44:00Z", "published", 7.8, "3.1", ["CWE-665"]),
    ("CVE-2023-4863", "2023-09-12T15:15:00Z", "published", 8.8, "3.1", ["CWE-787"]),
    ("CVE-2024-3400", "2024-04-12T08:15:00Z", "published", 10.0, "3.1", ["CWE-77"]),
    ("CVE-2010-0001", "2010-01-01T00:00:00Z", "published", 9.3, "2.0", []),
    ("CVE-2024-9999", "2024-06-01T00:00:00Z", "rejected", None, None, []),
]
# cve, date_added, vendor, product, ransomware
KEV = [
    ("CVE-2021-44228", "2021-12-10", "Apache", "Log4j2", 1),
    ("CVE-2021-26855", "2021-11-03", "Microsoft", "Exchange Server", 1),
    ("CVE-2022-0847", "2022-04-25", "Linux", "Kernel", 0),
    ("CVE-2023-4863", "2023-09-13", "Google", "Chromium WebP", 0),
    ("CVE-2024-3400", "2024-04-12", "Palo Alto Networks", "PAN-OS", 1),
    ("CVE-2010-0001", "2022-03-01", "Microsoft", "Windows", None),
]
# cve, score, percentile  (CVE-2025-1111 has a score but no CVE record in this library)
EPSS = [("CVE-2021-44228", 0.97, 0.999), ("CVE-2021-26855", 0.95, 0.997), ("CVE-2022-0847", 0.12, 0.93),
        ("CVE-2023-4863", 0.6, 0.98), ("CVE-2024-3400", 0.94, 0.996), ("CVE-2025-1111", 0.91, 0.99)]


def publish(store, source, digest, write):
    store.upsert_source(source, "vuln", source, source, f"https://example.org/{source}")
    run = store.start_sync(source)
    snapshot = store.stage_snapshot(run, digest * 64, f"{source}/raw")
    with store.write_staged(snapshot) as db:
        write(db, snapshot)
    store.publish(run)
    return snapshot


def write_vulnerabilities(rows):
    def write(db, snapshot):
        for cve, published, status, cvss, version, cwes in rows:
            db.execute("""INSERT INTO vulnerabilities (snapshot_id, cve_id, published_at, modified_at, status, description,
                          cvss_base, cvss_version, cvss_vector, cvss_origin, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                       (snapshot, cve, published, published, status, f"Description of {cve}", cvss, version, None, "cna", "cna"))
            for cwe in cwes:
                db.execute("INSERT INTO vulnerability_weaknesses VALUES (?,?,?,?)", (snapshot, cve, cwe, "cna"))
            db.execute("INSERT INTO vulnerability_references VALUES (?,?,?,?,?)",
                       (snapshot, cve, f"https://example.org/advisory/{cve}", '["vendor-advisory"]', "cna"))
        db.execute("""INSERT INTO affected (snapshot_id, record_id, cve_id, advisory_id, ecosystem, package_name, range_type,
                      events_json, versions_json, origin) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (snapshot, "USN-1", "CVE-2022-0847", "USN-5317-1", "Ubuntu:20.04:LTS", "linux", "ECOSYSTEM",
                    '[{"introduced": "0"}, {"fixed": "5.4.0-104.118"}]', "[]", "osv"))
    return write


def write_kev(rows):
    def write(db, snapshot):
        for cve, added, vendor, product, ransomware in rows:
            db.execute("""INSERT INTO kev (snapshot_id, cve_id, date_added, vendor, vendor_key, product, product_key,
                          known_ransomware_use, ransomware_known, required_action, due_date, notes, origin)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (snapshot, cve, added, vendor, vendor.casefold(), product, product.casefold(),
                        None, ransomware, "Apply updates.", None, None, "cisa"))
    return write


def write_epss(rows):
    def write(db, snapshot):
        for cve, score, percentile in rows:
            db.execute("INSERT INTO epss VALUES (?,?,?,?,?,?)", (snapshot, cve, score, percentile, "2026-11-10", "first"))
    return write


def synthetic_store(path):
    store = KnowledgeStore(path)
    store.upsert_space("vuln", "Vulnerabilidades")
    publish(store, "cvelist", "a", write_vulnerabilities(VULNERABILITIES))
    publish(store, "kev", "b", write_kev(KEV))
    publish(store, "epss", "c", write_epss(EPSS))
    return store


class RecordSearchTests(unittest.TestCase):
    def setUp(self):
        self.store = synthetic_store(Path(tempfile.mkdtemp(prefix="record-search-")) / "knowledge.sqlite3")
        self.vocabulary = record_search.vocabulary(self.store, RECORD_SET)

    def run_question(self, question):
        plan = record_engine.plan(question, RECORD_SET, today=TODAY, vocabulary=self.vocabulary)
        self.assertIsInstance(plan, record_engine.Plan, (question, plan))
        return record_search.search(self.store, plan, RECORD_SET)

    def ids(self, question):
        return [record["id"] for record in self.run_question(question)["records"]]

    def test_vocabulary_comes_from_the_published_catalog(self):
        self.assertEqual(self.vocabulary["microsoft"], ["exchange server", "windows"])
        self.assertIn("palo alto networks", self.vocabulary)

    def test_lookup_returns_the_record_with_every_source_and_reports_missing_ids(self):
        result = self.run_question("Compara CVE-2022-0847 con CVE-2099-0001")
        record, = result["records"]
        self.assertEqual(result["missing"], ["CVE-2099-0001"])
        self.assertEqual((record["cvss_base"], record["in_kev"], record["kev_vendor"], record["epss_score"]), (7.8, 1, "Linux", 0.12))
        self.assertEqual(record["cwe"], ["CWE-665"])
        self.assertEqual(record["affected"][0]["package_name"], "linux")
        self.assertEqual(record["affected"][0]["events_json"][1], {"fixed": "5.4.0-104.118"})
        self.assertEqual(record["references"], ["https://example.org/advisory/CVE-2022-0847"])
        self.assertEqual([source["source_id"] for source in result["sources"]], ["cvelist", "epss", "kev"])

    def test_lookup_shows_a_rejected_record_but_filters_never_do(self):
        record, = self.run_question("CVE-2024-9999")["records"]
        self.assertEqual(record["status"], "rejected")
        self.assertNotIn("CVE-2024-9999", self.ids("CVE publicadas en 2024 con CVSS superior a 0"))
        # A score without a CVE record is still a known identifier.
        self.assertEqual(self.run_question("CVE-2025-1111")["records"][0]["status"], None)

    def test_filters_match_hand_written_expectations(self):
        cases = {
            "Vulnerabilidades en KEV usadas en campañas de ransomware": {"CVE-2021-44228", "CVE-2021-26855", "CVE-2024-3400"},
            "Vulnerabilidades en KEV que no se usan en ransomware": {"CVE-2022-0847", "CVE-2023-4863", "CVE-2010-0001"},
            "Vulnerabilidades explotadas activamente de Microsoft": {"CVE-2021-26855", "CVE-2010-0001"},
            "Known exploited vulnerabilities in Microsoft Exchange Server": {"CVE-2021-26855"},
            "known exploited vulnerabilities in Palo Alto Networks PAN-OS": {"CVE-2024-3400"},
            "Vulnerabilidades añadidas a KEV en 2022": {"CVE-2022-0847", "CVE-2010-0001"},
            "CVE publicadas en 2021 con explotación conocida": {"CVE-2021-44228", "CVE-2021-26855"},
            "Vulnerabilidades de tipo CWE-787 con explotación conocida": {"CVE-2023-4863"},
            "Vulnerabilidades que afectan a Ubuntu con explotación conocida": {"CVE-2022-0847"},
            # Scores exist for a CVE without a record here; it still matches a pure EPSS filter.
            "Vulnerabilidades con EPSS superior al 90 %": {"CVE-2021-44228", "CVE-2021-26855", "CVE-2024-3400", "CVE-2025-1111"},
            "CVEs with an EPSS score above 0.9 that are in KEV": {"CVE-2021-44228", "CVE-2021-26855", "CVE-2024-3400"},
            "Vulnerabilidades por encima del percentil 99,5 de EPSS": {"CVE-2021-44228", "CVE-2021-26855", "CVE-2024-3400"},
        }
        for question, expected in cases.items():
            self.assertEqual(set(self.ids(question)), expected, question)

    def test_strict_and_inclusive_comparisons_differ(self):
        self.assertNotIn("CVE-2023-4863", self.ids("Vulnerabilidades con EPSS mayor que 0,6"))
        self.assertIn("CVE-2023-4863", self.ids("Vulnerabilidades con EPSS mayor o igual a 0,6"))

    def test_severity_bands_ignore_cvss_v2_scores(self):
        self.assertEqual(set(self.ids("Vulnerabilidades críticas con explotación conocida")),
                         {"CVE-2021-44228", "CVE-2021-26855", "CVE-2024-3400"})
        self.assertIn("CVE-2010-0001", self.ids("Vulnerabilidades con CVSS de 9 o más"))

    def test_ordering_and_limit_are_deterministic_and_total_counts_every_match(self):
        result = self.run_question("Las 2 vulnerabilidades con mayor EPSS")
        self.assertEqual([record["id"] for record in result["records"]], ["CVE-2021-44228", "CVE-2021-26855"])
        self.assertEqual((result["limit"], result["total"]), (2, 6))  # only records that have a score are ranked
        self.assertEqual(self.ids("Vulnerabilidades recientes con explotación conocida")[:2], ["CVE-2024-3400", "CVE-2023-4863"])
        # Equal scores break on the identifier.
        self.assertEqual(self.ids("Top 3 known exploited vulnerabilities by CVSS score"),
                         ["CVE-2024-3400", "CVE-2021-44228", "CVE-2021-26855"])
        self.assertNotIn("CVE-2010-0001", self.ids("Las 10 vulnerabilidades con mayor EPSS"))   # in KEV, never scored

    def test_counts_and_groupings(self):
        self.assertEqual(self.run_question("¿Cuántas vulnerabilidades hay en KEV?")["total"], 6)
        self.assertEqual(self.run_question("How many known exploited vulnerabilities does Microsoft have?")["total"], 2)
        groups = self.run_question("¿Qué fabricantes tienen más vulnerabilidades con explotación conocida?")["groups"]
        self.assertEqual((groups[0]["key"], groups[0]["label"], groups[0]["count"]), ("microsoft", "Microsoft", 2))
        years = self.run_question("Número de vulnerabilidades añadidas a KEV por año")["groups"]
        self.assertEqual({row["key"]: row["count"] for row in years}, {"2021": 2, "2022": 2, "2023": 1, "2024": 1})
        cwes = self.run_question("Most common CWEs among known exploited vulnerabilities")["groups"]
        self.assertEqual(sum(row["count"] for row in cwes), 6)
        months = self.run_question("Evolución mensual de las altas en KEV durante 2022")["groups"]
        self.assertEqual([row["key"] for row in months], ["2022-03", "2022-04"])
        products = self.run_question("Agrupa por producto las vulnerabilidades de Microsoft")["groups"]
        self.assertEqual({row["label"] for row in products}, {"Exchange Server", "Windows"})
        ecosystems = self.run_question("Agrupa por ecosistema las vulnerabilidades con CVSS de 7 o más")["groups"]
        self.assertEqual([(row["key"], row["count"]) for row in ecosystems], [("Ubuntu:20.04:LTS", 1)])

    def test_a_staged_or_failed_snapshot_is_never_visible(self):
        run = self.store.start_sync("kev")
        snapshot = self.store.stage_snapshot(run, "d" * 64, "kev/next")
        with self.store.write_staged(snapshot) as db:
            write_kev([("CVE-2025-1111", "2025-01-01", "Evil", "Staged", 1)])(db, snapshot)
        self.assertEqual(self.run_question("¿Cuántas vulnerabilidades hay en KEV?")["total"], 6)
        self.store.fail_sync(run, "test", "abandoned on purpose")
        self.assertEqual(self.run_question("¿Cuántas vulnerabilidades hay en KEV?")["total"], 6)
        self.assertNotIn("evil", record_search.vocabulary(self.store, RECORD_SET))

    def test_question_text_never_reaches_sql_as_code(self):
        hostile = record_engine.Plan("vulnerabilities", "filter", conditions=(
            record_engine.Condition("vendor", "eq", "x' OR 1=1 --"), record_engine.Condition("product", "contains", "%"),
            record_engine.Condition("cwe", "in", ("CWE-1') OR 1=1 --",))))
        self.assertEqual(record_search.search(self.store, hostile, RECORD_SET)["total"], 0)
        self.assertEqual(record_search.search(self.store, record_engine.Plan("vulnerabilities", "lookup", ids=("x' OR '1'='1",)), RECORD_SET)["total"], 0)
        with self.assertRaises(KeyError):
            record_search.search(self.store, record_engine.Plan("vulnerabilities", "filter", conditions=(
                record_engine.Condition("not_a_field", "eq", 1),)), RECORD_SET)


if __name__ == "__main__":
    unittest.main()
