"""The tender listing, the download of what is chosen and the withdrawal. Offline: the platform is a dictionary."""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["RAG_RERANKER"] = ""

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from pypdf import PdfWriter
import rag
import tenders
from web_app import RagService, create_app

FEED = "https://plataforma.example/feed.atom"
OLDER = "https://plataforma.example/feed_1.atom"
DOCUMENTS = "https://documentos.example/get?id="
CONFIG = {
    "id": "pliegos", "name": {"es": "Pliegos", "en": "Tender documents"}, "category": ["Pliegos", "Prueba"],
    "source": {"name": "Plataforma de prueba", "feed": FEED, "document_hosts": ["documentos.example"]},
    "filter": {"contract_types": ["2"], "security_cpv_prefixes": ["7281"], "it_cpv_prefixes": ["72"],
               "excluded_cpv_prefixes": ["45"], "unambiguous_terms": ["ciberseguridad"], "acronyms": ["SOC"],
               "it_only_terms": ["cortafuegos"], "excluded_terms": ["seguridad vial"],
               "statuses": ["PUB", "EV"], "deadline_from": "2026-01-01"},
    "documents": ["technical", "legal"],
}
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def pdf():
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    return buffer.getvalue()


def entry(folder, title, updated, *, status="PUB", deadline="2026-12-01", kind="2", cpv="72000000", technical=True):
    documents = (f"<cac:TechnicalDocumentReference><cbc:ID>PPT {folder}.pdf</cbc:ID><cbc:URI>{DOCUMENTS}{folder}-ppt</cbc:URI>"
                 "</cac:TechnicalDocumentReference>" if technical else "")
    documents += (f"<cac:LegalDocumentReference><cbc:ID>PCAP {folder}.pdf</cbc:ID><cbc:URI>{DOCUMENTS}{folder}-pcap</cbc:URI>"
                  "</cac:LegalDocumentReference>")
    return (f"<entry><id>https://plataforma.example/{folder}</id><link href=\"https://plataforma.example/ver/{folder}\"/>"
            f"<title>{title}</title><updated>{updated.isoformat()}</updated><cbc:ContractFolderID>{folder}</cbc:ContractFolderID>"
            f"<x:ContractFolderStatusCode>{status}</x:ContractFolderStatusCode>"
            "<cac:PartyName><cbc:Name>Ayuntamiento de Ejemplo</cbc:Name></cac:PartyName>"
            f"<cac:ProcurementProject><cbc:TypeCode>{kind}</cbc:TypeCode>"
            "<cac:BudgetAmount><cbc:TaxExclusiveAmount>1000</cbc:TaxExclusiveAmount></cac:BudgetAmount>"
            f"<cbc:ItemClassificationCode>{cpv}</cbc:ItemClassificationCode>"
            "<cac:RealizedLocation><cbc:CountrySubentity>Murcia</cbc:CountrySubentity>"
            "<cbc:CountrySubentityCode>ES620</cbc:CountrySubentityCode></cac:RealizedLocation></cac:ProcurementProject>"
            f"<cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>{deadline}</cbc:EndDate></cac:TenderSubmissionDeadlinePeriod>"
            f"{documents}</entry>")


def page(*items, following=None):
    link = f'<link href="{following}" rel="next"/>' if following else ""
    return ("<feed>" + link + "".join(items) + "</feed>").encode()


def platform(pages, calls=None):
    """A reader over fixed pages and documents; `calls` collects what was asked for."""
    def read(url, *, limit=None, timeout=None):
        if calls is not None:
            calls.append(url)
        if url in pages:
            return pages[url]
        if url.startswith(DOCUMENTS):
            return pdf()
        raise OSError(f"sin respuesta: {url}")
    return read


def hours(count):
    return NOW - timedelta(hours=count)


class FramingTests(unittest.TestCase):
    def test_transfer_framing_left_in_the_body_is_removed(self):
        self.assertEqual(tenders.unframe(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"), b"hello world")
        # Ordinary content is returned as it is, also when it merely starts with something like a size.
        for data in (b"%PDF-1.4 content", b"<feed></feed>", b"5\r\nhel", b"12\r\nshort\r\n0\r\n\r\n"):
            self.assertEqual(tenders.unframe(data), data)

    def test_a_feed_page_loses_framing_found_in_the_middle_of_a_value(self):
        text = tenders.feed_text(b"<updated>2026-1\r\n3fda\r\n1-11T20:04:23+01:00</updated>")
        self.assertEqual(text, "<updated>2026-11-11T20:04:23+01:00</updated>")


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.pages = {
            FEED: page(entry("A/1", "Servicio de ciberseguridad gestionada", hours(1)),
                       entry("B/2", "Mantenimiento de jardines", hours(2)),
                       entry("C/3", "Suministro de licencias de ciberseguridad", hours(3), kind="1"), following=OLDER),
            OLDER: page(entry("D/4", "Centro de operaciones SOC", hours(30), status="ADJ"),
                        entry("A/1", "Servicio de ciberseguridad", hours(40), status="PRE"),
                        entry("E/5", "Auditoría de ciberseguridad", hours(100)), following="https://plataforma.example/feed_2.atom"),
        }

    def refresh(self, catalog, registered=None, days=3, **options):
        calls = []
        result = tenders.refresh(CONFIG, catalog, registered or {}, platform(self.pages, calls), days=days, now=NOW,
                                 sleep=lambda seconds: None, **options)
        return result, calls

    def test_the_listing_keeps_what_the_profile_is_about_in_any_state(self):
        catalog = {}
        result, calls = self.refresh(catalog)
        self.assertEqual(calls, [FEED, OLDER])           # the second page crosses the three days: reading stops there
        self.assertTrue(result["complete"])
        self.assertEqual(sorted(catalog["tenders"]), ["A/1", "D/4", "E/5"])
        # The newest entry of a tender is its current state; an older one does not replace it.
        self.assertEqual(catalog["tenders"]["A/1"]["status"], "PUB")
        self.assertEqual(catalog["tenders"]["A/1"]["title"], "Servicio de ciberseguridad gestionada")
        self.assertEqual(catalog["tenders"]["D/4"]["status"], "ADJ")
        self.assertEqual([item["kind"] for item in catalog["tenders"]["A/1"]["documents"]], ["technical", "legal"])
        self.assertEqual(catalog["newest"], hours(1).isoformat())
        self.assertEqual(catalog["oldest"], hours(100).isoformat())

    def test_a_later_reading_stops_at_what_is_already_listed(self):
        catalog = {}
        self.refresh(catalog)
        self.pages[FEED] = page(entry("F/6", "Respuesta a incidentes de ciberseguridad", NOW), entry("A/1", "Servicio de ciberseguridad gestionada", hours(1)),
                                following=OLDER)
        result, calls = self.refresh(catalog)
        self.assertEqual(calls, [FEED])
        self.assertEqual(result["found"], 1)
        self.assertIn("F/6", catalog["tenders"])
        self.assertEqual(catalog["newest"], NOW.isoformat())
        self.assertEqual(catalog["oldest"], hours(100).isoformat())

    def test_a_stopped_reading_keeps_its_place_and_the_next_one_goes_on(self):
        catalog, seen = {}, []
        result, calls = self.refresh(catalog, cancelled=lambda: bool(seen), progress=lambda **state: seen.append(state))
        self.assertEqual(calls, [FEED])
        self.assertFalse(result["complete"])
        self.assertIn("A/1", catalog["tenders"])
        self.assertEqual((catalog["oldest"], catalog["older"]), (hours(3).isoformat(), OLDER))
        # The next one reads what is new at the head, then continues after the page already read.
        result, calls = self.refresh(catalog)
        self.assertEqual(calls, [FEED, OLDER])
        self.assertTrue(result["complete"])
        self.assertEqual(sorted(catalog["tenders"]), ["A/1", "D/4", "E/5"])

    def test_a_longer_period_continues_after_what_is_listed_instead_of_reading_it_again(self):
        third, fourth = "https://plataforma.example/feed_2.atom", "https://plataforma.example/feed_3.atom"
        self.pages[OLDER] = page(entry("D/4", "Centro de operaciones SOC", hours(30), status="ADJ"),
                                 entry("E/5", "Auditoría de ciberseguridad", hours(60)), following=third)
        self.pages[third] = page(entry("H/8", "Hacking ético y ciberseguridad", hours(100)), following=fourth)
        self.pages[fourth] = page(entry("I/9", "Plan de ciberseguridad", hours(300)))
        catalog = {}
        result, calls = self.refresh(catalog, days=2)
        self.assertEqual(calls, [FEED, OLDER])              # two days end inside the second page
        self.assertEqual(catalog["older"], third)
        result, calls = self.refresh(catalog, days=10)
        self.assertEqual(calls, [FEED, third, fourth])      # the second page is not asked for again
        self.assertEqual(sorted(catalog["tenders"]), ["A/1", "D/4", "E/5", "H/8", "I/9"])
        self.assertEqual(catalog["oldest"], hours(300).isoformat())
        # A period the listing already covers needs only the head of the feed.
        self.assertEqual(self.refresh(catalog, days=10)[1], [FEED])

    def test_a_continuation_page_that_is_gone_is_read_around(self):
        third = "https://plataforma.example/feed_2.atom"
        self.pages[OLDER] = page(entry("D/4", "Centro de operaciones SOC", hours(30), status="ADJ"),
                                 entry("E/5", "Auditoría de ciberseguridad", hours(60)), following=third)
        self.pages[third] = page(entry("H/8", "Hacking ético y ciberseguridad", hours(100)))
        catalog = {}
        self.refresh(catalog, days=2)
        catalog["older"] = "https://plataforma.example/ya-no-existe.atom"
        result, calls = self.refresh(catalog, days=10)
        self.assertEqual(calls, [FEED, "https://plataforma.example/ya-no-existe.atom", OLDER, third])
        self.assertTrue(result["complete"])
        self.assertIn("H/8", catalog["tenders"])

    def test_a_failure_keeps_what_was_read_and_says_so(self):
        del self.pages[OLDER]
        catalog = {}
        result, calls = self.refresh(catalog, registered={"A/1": hours(200).isoformat()})
        self.assertEqual(calls, [FEED, OLDER])
        self.assertFalse(result["complete"])
        self.assertIn("sin respuesta", result["error"])
        self.assertIn("A/1", catalog["tenders"])
        self.assertEqual(list(result["changes"]), ["A/1"])
        self.assertEqual(catalog["older"], OLDER)

    def test_newer_data_about_a_tender_in_the_library_is_reported(self):
        result, _ = self.refresh({}, registered={"D/4": hours(200).isoformat(), "A/1": hours(1).isoformat()})
        self.assertEqual(list(result["changes"]), ["D/4"])
        self.assertEqual(result["changes"]["D/4"]["status"], "ADJ")

    def test_only_open_tenders_are_shown_as_open(self):
        catalog = {}
        self.refresh(catalog)
        rows = {item["key"]: item for item in tenders.view(catalog, {"tenders": {}}, NOW.date())["tenders"]}
        self.assertTrue(rows["A/1"]["open"])
        self.assertFalse(rows["D/4"]["open"])
        self.assertEqual(rows["A/1"]["region"], "Región de Murcia")
        self.assertEqual((rows["A/1"]["technical"], rows["A/1"]["legal"]), (1, 1))
        expired = {**catalog["tenders"]["A/1"], "deadline": "2026-11-01"}
        self.assertFalse(tenders.is_open(expired, NOW.date()))

    def test_a_link_to_another_site_is_not_followed(self):
        self.pages[FEED] = page(entry("A/1", "Servicio de ciberseguridad gestionada", hours(1)), following="https://otro.example/feed.atom")
        result, calls = self.refresh({})
        self.assertEqual(calls, [FEED])

    def test_the_reader_only_opens_the_hosts_of_the_profile(self):
        read = tenders.reader(CONFIG)
        for url in ("https://otro.example/x", "http://plataforma.example/feed.atom", "file:///etc/passwd"):
            with self.assertRaises(tenders.TenderError):
                read(url)


class OfflineService(RagService):
    def model_capabilities(self, model):
        return []

    def ollama_status(self):
        return {"connected": True, "models": ["test-local:latest"], "default_model": "test-local:latest", "loaded": []}

    def get_embeddings(self, download=False):
        if self.embeddings is None:
            self.embeddings = DeterministicFakeEmbedding(size=32)
        return self.embeddings


class TenderApiTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / "data" / "test-chroma"
        root.mkdir(parents=True, exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix="tenders-", dir=root))
        (self.case / "profiles" / "prueba").mkdir(parents=True)
        (self.case / "profiles" / "prueba" / "pliegos.json").write_text(json.dumps(CONFIG), encoding="utf-8")
        self.service = OfflineService(rag.Config(db=self.case / "db", collection="tenders_test"), self.case / "documents")
        self.service.tender_root = self.case
        now = datetime.now(timezone.utc)
        self.pages = {FEED: page(entry("A/1", "Servicio de ciberseguridad gestionada", now - timedelta(hours=1)),
                                 entry("D/4", "Centro de operaciones SOC", now - timedelta(hours=2)),
                                 entry("G/7", "Cortafuegos sin pliego técnico", now - timedelta(hours=3), technical=False),
                                 entry("Z/9", "Ciberseguridad de hace tiempo", now - timedelta(days=30)))}
        self.calls = []
        self.service.tender_reader = lambda config: platform(self.pages, self.calls)
        self.client = TestClient(create_app(self.service), client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.headers = {"X-RAG-Token": self.client.get("/api/session").json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def wait(self, job_id, expected="done"):
        for _ in range(400):
            result = self.client.get(f"/api/jobs/{job_id}").json()
            if result["status"] in {"done", "error", "cancelled"}:
                self.assertEqual(result["status"], expected, result)
                return result
            time.sleep(0.05)
        self.fail("Job did not complete")

    def post(self, path, **body):
        return self.client.post(path, headers=self.headers, json=body)

    def listing(self):
        return self.client.get("/api/tenders").json()

    def read_platform(self):
        response = self.post("/api/admin/tenders/refresh", profile="prueba", days=3)
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait(response.json()["job_id"])

    def download(self, *keys):
        response = self.post("/api/admin/tenders/download", profile="prueba", keys=list(keys))
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait(response.json()["job_id"])

    def test_nothing_is_downloaded_until_the_user_chooses(self):
        before = self.listing()
        self.assertEqual([item["profile"] for item in before["sources"]], ["prueba"])
        self.assertEqual(before["tenders"], [])
        job = self.read_platform()
        self.assertEqual(job["tender_reading"]["found"], 4)
        after = self.listing()
        self.assertEqual(sorted(item["key"] for item in after["tenders"]), ["A/1", "D/4", "G/7", "Z/9"])
        self.assertTrue(after["read_at"])
        self.assertFalse(any(item["in_library"] for item in after["tenders"]))
        # Reading the listing asked the platform for its feed and for no document.
        self.assertEqual(self.calls, [FEED])
        self.assertEqual([path.name for path in (self.case / "documents").iterdir()], [])

    def test_the_chosen_tenders_are_downloaded_filed_and_indexed(self):
        self.read_platform()
        job = self.download("A/1", "G/7")
        outcomes = {item["key"]: item for item in job["tender_outcomes"]}
        self.assertTrue(outcomes["A/1"]["downloaded"])
        self.assertEqual(outcomes["G/7"], {"key": "G/7", "downloaded": False, "reason": "no technical document"})
        names = sorted(path.name for path in (self.case / "documents").iterdir())
        self.assertEqual(names, ["A_1__FICHA.md", "A_1__PCAP__PCAP_A_1.pdf", "A_1__PPT__PPT_A_1.pdf"])
        self.assertIn("Comunidad autónoma: Región de Murcia", (self.case / "documents" / "A_1__FICHA.md").read_text(encoding="utf-8"))
        status = self.client.get("/api/status").json()
        documents = {item["name"]: item for item in status["documents"]}
        self.assertTrue(documents["A_1__FICHA.md"]["indexed"])
        topics = {item["id"]: item for item in status["library"]["topics"]}
        topic = topics[documents["A_1__FICHA.md"]["topic_id"]]
        self.assertEqual((topics[topic["parent_id"]]["name"], topic["name"]), ("Pliegos", "Prueba"))
        self.assertEqual({documents[name]["topic_id"] for name in names}, {topic["id"]})
        row = next(item for item in self.listing()["tenders"] if item["key"] == "A/1")
        self.assertTrue(row["in_library"])
        self.assertEqual(row["files"], 3)
        self.assertNotIn(f"{DOCUMENTS}D/4-ppt", self.calls)

    def test_a_tender_that_is_not_listed_cannot_be_downloaded(self):
        self.read_platform()
        response = self.post("/api/admin/tenders/download", profile="prueba", keys=["NO/1"])
        job = self.wait(response.json()["job_id"], expected="error")
        self.assertEqual(job["error_info"]["code"], "TENDER_UNKNOWN")
        self.assertEqual(self.post("/api/admin/tenders/download", profile="otro", keys=["A/1"]).status_code, 404)
        self.assertEqual(self.post("/api/admin/tenders/download", profile="../x", keys=["A/1"]).status_code, 404)

    def test_a_change_of_state_renews_the_sheet_of_a_tender_in_the_library(self):
        self.read_platform()
        self.download("A/1")
        sheet = self.case / "documents" / "A_1__FICHA.md"
        self.assertIn("Abierta para presentar ofertas: sí", sheet.read_text(encoding="utf-8"))
        self.pages[FEED] = page(entry("A/1", "Servicio de ciberseguridad gestionada", datetime.now(timezone.utc), status="ADJ"))
        job = self.read_platform()
        self.assertEqual(job["tender_reading"]["renewed"], 1)
        self.assertIn("Abierta para presentar ofertas: no (adjudicada)", sheet.read_text(encoding="utf-8"))
        for _ in range(200):
            if not self.client.get("/api/jobs?active=1").json()["jobs"]:
                break
            time.sleep(0.05)
        documents = {item["name"]: item for item in self.client.get("/api/status").json()["documents"]}
        self.assertTrue(documents["A_1__FICHA.md"]["indexed"])
        row = next(item for item in self.listing()["tenders"] if item["key"] == "A/1")
        self.assertEqual((row["status"], row["library_status"], row["open"]), ("ADJ", "ADJ", False))

    def test_withdrawn_tenders_go_to_a_backup_and_leave_the_index(self):
        self.read_platform()
        self.download("A/1", "D/4")
        chunks = self.client.get("/api/status").json()["chunk_count"]
        self.assertGreater(chunks, 0)
        self.assertEqual(self.post("/api/admin/tenders/withdraw", confirm="no", keys=["A/1"]).status_code, 422)
        response = self.post("/api/admin/tenders/withdraw", confirm="withdraw-tenders", keys=["A/1"])
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result["tenders"], result["documents"]), (1, 3))
        backup = rag.ROOT / result["backup"]
        self.assertEqual(sorted(path.name for path in (backup / "documents").iterdir()),
                         ["A_1__FICHA.md", "A_1__PCAP__PCAP_A_1.pdf", "A_1__PPT__PPT_A_1.pdf"])
        self.assertIn("A/1", json.loads((backup / "retirados.json").read_text(encoding="utf-8"))["tenders"])
        status = self.client.get("/api/status").json()
        self.assertEqual(sorted(item["name"] for item in status["documents"]),
                         ["D_4__FICHA.md", "D_4__PCAP__PCAP_D_4.pdf", "D_4__PPT__PPT_D_4.pdf"])
        self.assertLess(status["chunk_count"], chunks)
        manifest = json.loads((self.case / "db" / "tenders_test-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(record["file"] for record in manifest["files"].values() if record["file"].startswith("A_1")), [])
        rows = {item["key"]: item for item in self.listing()["tenders"]}
        self.assertFalse(rows["A/1"]["in_library"])
        self.assertTrue(rows["D/4"]["in_library"])
        self.assertEqual(self.post("/api/admin/tenders/withdraw", confirm="withdraw-tenders", keys=["A/1"]).status_code, 404)

    def test_withdrawing_needs_the_server_host(self):
        self.read_platform()
        self.download("A/1")
        remote = TestClient(self.client.app, client=("192.168.1.50", 50000))
        response = remote.post("/api/admin/tenders/withdraw", headers=self.headers, json={"confirm": "withdraw-tenders", "keys": ["A/1"]})
        self.assertEqual(response.status_code, 403)
        self.assertTrue((self.case / "documents" / "A_1__FICHA.md").exists())


if __name__ == "__main__":
    unittest.main()
