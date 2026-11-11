"""Public-source connectors validate offline fixtures before publication."""
from contextlib import redirect_stdout
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_connectors.common import ConnectorError, Download
from knowledge_connectors.cve import CVEListConnector, record_url
from knowledge_connectors.epss import EPSSConnector
from knowledge_connectors.kev import KEVConnector
from knowledge_store import KnowledgeStore
from tools import sync_corpus


def kev_bytes(*identifiers):
    return json.dumps({
        "catalogVersion": "2026.11.11",
        "dateReleased": "2026-11-11T12:00:00Z",
        "count": len(identifiers),
        "vulnerabilities": [{
            "cveID": identifier,
            "vendorProject": "Example Vendor",
            "product": "Example Product",
            "dateAdded": "2026-11-01",
            "shortDescription": "Example",
            "requiredAction": "Apply the vendor update.",
            "dueDate": "2026-11-20",
            "knownRansomwareCampaignUse": "Known" if index == 0 else "Unknown",
            "notes": "https://example.test/advisory",
        } for index, identifier in enumerate(identifiers)],
    }).encode()


def epss_bytes():
    content = (
        "#model_version:v2026.06.15,score_date:2026-11-11\n"
        "cve,epss,percentile\n"
        "CVE-2024-0001,0.750000,0.980000\n"
        "CVE-2024-10001,0.125000,0.700000\n"
    ).encode()
    return gzip.compress(content)


def cve_bytes(identifier, *, description="Memory corruption in the example component."):
    return json.dumps({
        "dataType": "CVE_RECORD",
        "dataVersion": "5.1",
        "cveMetadata": {
            "cveId": identifier,
            "state": "PUBLISHED",
            "datePublished": "2024-01-02T00:00:00Z",
            "dateUpdated": "2026-10-20T00:00:00Z",
        },
        "containers": {
            "cna": {
                "providerMetadata": {"shortName": "ExampleCNA"},
                "descriptions": [{"lang": "en", "value": description}],
                "problemTypes": [{"descriptions": [
                    {"lang": "en", "cweId": "CWE-787", "description": "Out-of-bounds Write"}
                ]}],
                "metrics": [{"cvssV3_1": {
                    "baseScore": 8.8,
                    "vectorString": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H",
                }}],
                "references": [
                    {"url": "https://example.test/advisory", "tags": ["vendor-advisory"]},
                    {"url": "https://example.test/advisory", "tags": ["patch"]},
                ],
            },
            "adp": [{
                "providerMetadata": {"shortName": "CVE"},
                "references": [{"url": "https://example.test/advisory", "tags": ["transferred"]}],
            }],
        },
    }).encode()


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return Download(value, url, url, etag='"fixture"', last_modified="Wed, 11 Nov 2026 12:00:00 GMT")


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = KnowledgeStore(self.root / "knowledge.sqlite3")
        self.raw = self.root / "raw"

    def tearDown(self):
        self.directory.cleanup()

    def sync_kev(self, *identifiers):
        client = FakeClient({KEVConnector.source_url: kev_bytes(*identifiers)})
        return KEVConnector().sync(self.store, self.raw, client=client)

    def test_kev_and_epss_publish_normalized_records_and_keep_raw_bytes(self):
        kev = self.sync_kev("CVE-2024-0001", "CVE-2024-10001")
        epss_client = FakeClient({EPSSConnector.source_url: epss_bytes()})
        epss = EPSSConnector().sync(self.store, self.raw, client=epss_client)
        self.assertEqual((kev.status, kev.records), ("published", 2))
        self.assertEqual((epss.status, epss.records), ("published", 2))
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT cve_id,vendor_key,ransomware_known FROM current_kev ORDER BY cve_id"
            ).fetchall()
            scores = db.execute(
                "SELECT cve_id,score,score_date FROM current_epss ORDER BY cve_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("CVE-2024-0001", "example vendor", 1),
            ("CVE-2024-10001", "example vendor", 0),
        ])
        self.assertEqual([tuple(row) for row in scores], [
            ("CVE-2024-0001", 0.75, "2026-11-11"),
            ("CVE-2024-10001", 0.125, "2026-11-11"),
        ])
        for source in ("cisa-kev", "first-epss"):
            snapshot = self.store.published_snapshot(source)
            self.assertTrue(Path(snapshot["original_path"]).is_file())
            self.assertIn(snapshot["sha256"], Path(snapshot["original_path"]).parts)

        unchanged = KEVConnector().sync(
            self.store, self.raw,
            client=FakeClient({KEVConnector.source_url: kev_bytes("CVE-2024-0001", "CVE-2024-10001")}),
        )
        self.assertEqual(unchanged.status, "unchanged")
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM sync_runs WHERE source_id='cisa-kev' AND status='unchanged'"
            ).fetchone()[0], 1)

    def test_cve_list_fetches_only_published_kev_and_preserves_provenance(self):
        identifiers = ("CVE-2024-0001", "CVE-2024-10001")
        self.sync_kev(*identifiers)
        responses = {record_url(identifier): cve_bytes(identifier) for identifier in identifiers}
        client = FakeClient(responses)
        result = CVEListConnector().sync(self.store, self.raw, client=client)
        self.assertEqual((result.status, result.records), ("published", 2))
        self.assertEqual([call[0] for call in client.calls], [record_url(item) for item in identifiers])
        with self.store.connect() as db:
            records = db.execute(
                "SELECT cve_id,cvss_base,cvss_version,cvss_origin FROM current_vulnerabilities ORDER BY cve_id"
            ).fetchall()
            weaknesses = db.execute(
                "SELECT DISTINCT cve_id,cwe_id FROM current_vulnerability_weaknesses ORDER BY cve_id"
            ).fetchall()
            references = db.execute(
                "SELECT origin,tags_json FROM current_vulnerability_references WHERE cve_id=? ORDER BY origin",
                (identifiers[0],),
            ).fetchall()
        self.assertEqual([tuple(row) for row in records], [
            ("CVE-2024-0001", 8.8, "3.1", "CNA:ExampleCNA"),
            ("CVE-2024-10001", 8.8, "3.1", "CNA:ExampleCNA"),
        ])
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT DISTINCT status FROM current_vulnerabilities"
            ).fetchone()[0], "published")
        self.assertEqual([tuple(row) for row in weaknesses], [
            ("CVE-2024-0001", "CWE-787"), ("CVE-2024-10001", "CWE-787"),
        ])
        self.assertEqual([row[0] for row in references], ["ADP:CVE", "CNA:ExampleCNA"])
        self.assertEqual(json.loads(references[1][1]), ["patch", "vendor-advisory"])
        manifest = json.loads(Path(self.store.published_snapshot("cve-list-v5")["original_path"]).read_text())
        self.assertEqual([item["cve_id"] for item in manifest["records"]], list(identifiers))

    def test_invalid_update_fails_without_replacing_published_kev(self):
        first = self.sync_kev("CVE-2024-0001")
        invalid = json.dumps({"vulnerabilities": []}).encode()
        with self.assertRaises(ConnectorError):
            KEVConnector().sync(
                self.store, self.raw,
                client=FakeClient({KEVConnector.source_url: invalid}),
            )
        self.assertEqual(self.store.published_snapshot("cisa-kev")["id"], first.snapshot_id)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT cve_id FROM current_kev").fetchone()[0], "CVE-2024-0001")
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM sync_runs WHERE source_id='cisa-kev' AND status='failed'"
            ).fetchone()[0], 1)

    def test_connector_recovers_run_left_by_a_dead_process(self):
        connector = KEVConnector()
        connector.register(self.store)
        abandoned = self.store.start_sync("cisa-kev", run_id="dead-process")
        self.store.stage_snapshot(
            abandoned, "a" * 64, "raw/incomplete.json", snapshot_id="incomplete-snapshot",
        )
        result = connector.sync(
            self.store, self.raw,
            client=FakeClient({connector.source_url: kev_bytes("CVE-2024-0001")}),
        )
        self.assertEqual(result.status, "published")
        with self.store.connect() as db:
            recovered = db.execute(
                "SELECT status,error_code FROM sync_runs WHERE id='dead-process'"
            ).fetchone()
        self.assertEqual(tuple(recovered), ("failed", "abandoned"))

    def test_cli_requires_explicit_sources_and_status_is_local(self):
        with self.assertRaises(SystemExit):
            sync_corpus.parser().parse_args(["sync"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(sync_corpus.main(["--data-dir", str(self.root), "status"]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"sources": [], "spaces": []})


if __name__ == "__main__":
    unittest.main()
