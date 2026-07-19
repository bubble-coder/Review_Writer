import os
from pathlib import Path
import tempfile
import unittest

from review_writer.secret_store import SecretStore


class SecretStoreTests(unittest.TestCase):
    def test_session_secret_is_available_but_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secrets.json"
            store = SecretStore(path)

            store.set("model.api_key", "session-secret", persist=False)

            self.assertEqual(store.get("model.api_key"), "session-secret")
            self.assertNotIn("session-secret", path.read_text(encoding="utf-8"))
            self.assertIsNone(SecretStore(path).get("model.api_key"))

    @unittest.skipUnless(os.name == "nt", "DPAPI test requires Windows")
    def test_persistent_secret_is_dpapi_encrypted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secrets.json"
            store = SecretStore(path)

            store.set("ima.api_key", "persistent-sensitive-value", persist=True)

            self.assertEqual(SecretStore(path).get("ima.api_key"), "persistent-sensitive-value")
            self.assertNotIn("persistent-sensitive-value", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
