"""Structured knowledge publication stays isolated from the document library."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from knowledge_store import KnowledgeError, KnowledgeStore, SCHEMA_VERSION


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "knowledge.sqlite3"
        self.store = KnowledgeStore(self.path)
        self.store.upsert_space(
            "vulnerability-intelligence", "Vulnerability intelligence",
            vector_collection="vulnerability-intelligence",
        )
        self.store.upsert_source(
            "cisa-kev", "vulnerability-intelligence", "kev", "CISA KEV",
            "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
            license_name="CISA data",
            license_url="https://www.cisa.gov/about/website-policies",
        )

    def tearDown(self):
        self.directory.cleanup()

    def stage_kev(self, suffix, cve_id):
        run = self.store.start_sync("cisa-kev", run_id=f"run-{suffix}")
        snapshot = self.store.stage_snapshot(
            run, suffix * 64, f"raw/kev-{suffix}.json", snapshot_id=f"snapshot-{suffix}",
            metadata={"format": "json"},
        )
        with self.store.write_staged(snapshot) as db:
            db.execute(
                """INSERT INTO kev
                   (snapshot_id,cve_id,date_added,vendor,vendor_key,product,product_key,
                    known_ransomware_use,ransomware_known,required_action,due_date,notes,origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot, cve_id, "2026-01-01", "Vendor", "vendor", "Product", "product",
                 "Known", 1, "Apply updates", "2026-01-20", None, "CISA KEV"),
            )
        return run, snapshot

    def test_schema_is_versioned_and_persists_spaces_and_sources(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({
            "spaces", "sources", "snapshots", "sync_runs", "vulnerabilities",
            "vulnerability_weaknesses", "vulnerability_references", "kev", "epss", "affected",
        } <= tables)
        reopened = KnowledgeStore(self.path)
        self.assertEqual(reopened.list_spaces()[0]["id"], "vulnerability-intelligence")
        self.assertEqual(reopened.list_sources()[0]["published_snapshot_id"], None)

    def test_staged_rows_are_invisible_until_atomic_publication(self):
        run, snapshot = self.stage_kev("a", "CVE-2024-0001")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM current_kev").fetchone()[0], 0)
        self.assertIsNone(self.store.published_snapshot("cisa-kev"))
        self.assertEqual(self.store.publish(run, stats={"records": 1}), snapshot)
        with self.store.connect() as db:
            current = db.execute("SELECT cve_id FROM current_kev").fetchall()
            sync = db.execute("SELECT status,stats_json FROM sync_runs WHERE id=?", (run,)).fetchone()
        self.assertEqual([row[0] for row in current], ["CVE-2024-0001"])
        self.assertEqual(self.store.published_vendors(), [{"vendor_key": "vendor", "vendor": "Vendor"}])
        self.assertEqual(sync[0], "published")
        self.assertEqual(sync[1], '{"records":1}')
        self.assertEqual(self.store.published_snapshot("cisa-kev")["id"], snapshot)
        with self.assertRaises(KnowledgeError):
            with self.store.write_staged(snapshot):
                pass

    def test_failed_replacement_preserves_previous_publication(self):
        first_run, first = self.stage_kev("b", "CVE-2024-0002")
        self.store.publish(first_run)
        failed_run, failed = self.stage_kev("c", "CVE-2024-0003")
        self.store.fail_sync(failed_run, "network", "Connection failed")
        self.assertEqual(self.store.published_snapshot("cisa-kev")["id"], first)
        with self.store.connect() as db:
            visible = db.execute("SELECT cve_id FROM current_kev").fetchall()
            failed_status = db.execute("SELECT status FROM snapshots WHERE id=?", (failed,)).fetchone()[0]
        self.assertEqual([row[0] for row in visible], ["CVE-2024-0002"])
        self.assertEqual(failed_status, "failed")

    def test_new_publication_supersedes_old_snapshot_in_one_transaction(self):
        first_run, first = self.stage_kev("d", "CVE-2024-0004")
        self.store.publish(first_run)
        second_run, second = self.stage_kev("e", "CVE-2024-0005")
        self.store.publish(second_run)
        with self.store.connect() as db:
            statuses = dict(db.execute("SELECT id,status FROM snapshots"))
            visible = [row[0] for row in db.execute("SELECT cve_id FROM current_kev")]
        self.assertEqual(statuses[first], "superseded")
        self.assertEqual(statuses[second], "published")
        self.assertEqual(visible, ["CVE-2024-0005"])

    def test_one_running_sync_per_source_and_unchanged_run(self):
        run = self.store.start_sync("cisa-kev", run_id="run-open")
        with self.assertRaisesRegex(KnowledgeError, "already running"):
            self.store.start_sync("cisa-kev", run_id="run-second")
        self.store.finish_without_change(run, stats={"reason": "same-sha256"})
        next_run = self.store.start_sync("cisa-kev", run_id="run-next")
        self.store.fail_sync(next_run, "cancelled", "Stopped by operator", cancelled=True)
        with self.store.connect() as db:
            statuses = dict(db.execute("SELECT id,status FROM sync_runs"))
        self.assertEqual(statuses[run], "unchanged")
        self.assertEqual(statuses[next_run], "cancelled")

    def test_invalid_identity_urls_hashes_and_unversioned_databases_are_rejected(self):
        with self.assertRaises(KnowledgeError):
            self.store.upsert_source(
                "unsafe", "vulnerability-intelligence", "custom", "Unsafe",
                "http://user:secret@example.test/data",
            )
        run = self.store.start_sync("cisa-kev", run_id="run-invalid")
        with self.assertRaises(KnowledgeError):
            self.store.stage_snapshot(run, "not-a-digest", "raw/data")
        self.store.fail_sync(run, "invalid", "Invalid source payload")

        legacy = Path(self.directory.name) / "unversioned.sqlite3"
        with closing(sqlite3.connect(legacy)) as db:
            db.execute("CREATE TABLE foreign_data (value TEXT)")
            db.commit()
        with self.assertRaisesRegex(KnowledgeError, "unversioned"):
            KnowledgeStore(legacy)

        future = Path(self.directory.name) / "future.sqlite3"
        with closing(sqlite3.connect(future)) as db:
            db.execute("PRAGMA user_version=99")
            db.commit()
        with self.assertRaisesRegex(KnowledgeError, "Unsupported"):
            KnowledgeStore(future)


if __name__ == "__main__":
    unittest.main()
