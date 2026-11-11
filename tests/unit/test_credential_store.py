"""Encrypted credential persistence without any external service."""

from pathlib import Path
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
            self.assertEqual(store.get("ovh"), "secret-example-value")
            self.assertNotIn(b"secret-example-value", path.read_bytes())
            store.close()

            reopened = CredentialStore(path, system="linux")
            self.assertTrue(reopened.status()["locked"])
            with self.assertRaises(CredentialStoreLocked):
                reopened.get("ovh")
            with self.assertRaises(CredentialError):
                reopened.unlock("incorrect password value")
            reopened.unlock("correct horse battery staple")
            self.assertEqual(reopened.get("ovh"), "secret-example-value")
            state = reopened.delete("ovh")
            self.assertFalse(state["configured"]["ovh"])
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
