"""Encrypted credential persistence without any external service."""

from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from credential_store import CredentialError, CredentialStore, CredentialStoreLocked


class CredentialStoreTests(unittest.TestCase):
    def test_linux_store_requires_unlock_and_never_persists_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.sqlite3"
            store = CredentialStore(path, system="linux")
            self.assertTrue(store.status()["locked"])
            with self.assertRaises(CredentialStoreLocked):
                store.set("ovh", "secret-example-value")
            store.unlock("correct horse battery staple")
            store.set("ovh", "secret-example-value")
            self.assertTrue(store.status()["available"]["ovh"])
            self.assertEqual(store.get("ovh"), "secret-example-value")
            self.assertNotIn(b"secret-example-value", path.read_bytes())
            store.close()

            reopened = CredentialStore(path, system="linux")
            self.assertTrue(reopened.status()["locked"])
            self.assertFalse(reopened.status()["available"]["ovh"])
            self.assertEqual(reopened.status()["errors"], {})
            with self.assertRaises(CredentialStoreLocked):
                reopened.get("ovh")
            with self.assertRaises(CredentialError):
                reopened.unlock("incorrect password value")
            reopened.unlock("correct horse battery staple")
            self.assertEqual(reopened.get("ovh"), "secret-example-value")
            state = reopened.delete("ovh")
            self.assertFalse(state["configured"]["ovh"])
            reopened.close()

    def test_status_does_not_create_an_empty_store(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new" / "credentials.sqlite3"
            store = CredentialStore(path)
            state = store.status()
            self.assertFalse(any(state["configured"].values()))
            self.assertFalse(any(state["available"].values()))
            self.assertFalse(path.parent.exists())

    def test_unreadable_ciphertext_is_reported_and_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.sqlite3"
            store = CredentialStore(path, system="linux")
            store.unlock("correct horse battery staple")
            store.set("groq", "secret-example-value")
            with closing(sqlite3.connect(path)) as db, db:
                payload = bytearray(db.execute("SELECT payload FROM credentials WHERE provider='groq'").fetchone()[0])
                payload[-1] ^= 1
                db.execute("UPDATE credentials SET payload=? WHERE provider='groq'", (bytes(payload),))
            state = store.status()
            self.assertTrue(state["configured"]["groq"])
            self.assertFalse(state["available"]["groq"])
            self.assertIn("groq", state["errors"])
            self.assertNotIn("secret-example-value", str(state))
            store.set("groq", "replacement-example-value")
            self.assertTrue(store.status()["available"]["groq"])
            self.assertNotIn("groq", store.status()["errors"])
            store.close()

    @unittest.skipUnless(os.name == "nt", "Requires real Windows DPAPI")
    def test_windows_dpapi_status_checks_ciphertext_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.sqlite3"
            store = CredentialStore(path)
            store.set("groq", "windows-example-value")
            store.close()
            with closing(sqlite3.connect(path)) as db, db:
                payload = bytes(db.execute("SELECT payload FROM credentials WHERE provider='groq'").fetchone()[0])
                db.execute("UPDATE credentials SET payload=? WHERE provider='groq'", (payload[:-1],))
            before = path.read_bytes()
            reopened = CredentialStore(path)
            state = reopened.status()
            self.assertTrue(state["configured"]["groq"])
            self.assertFalse(state["available"]["groq"])
            self.assertIn("sesión de Windows", state["errors"]["groq"])
            self.assertEqual(path.read_bytes(), before)
            reopened.set("groq", "replacement-example-value")
            self.assertTrue(reopened.status()["available"]["groq"])
            reopened.close()

    def test_rejects_unknown_provider_and_malformed_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credentials.sqlite3", system="linux")
            store.unlock("a sufficiently long password")
            for provider, value in (("unknown", "long-enough-key"), ("xai", "short"), ("xai", "line\nbreak-secret")):
                with self.subTest(provider=provider, value=value), self.assertRaises(CredentialError):
                    store.set(provider, value)
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
