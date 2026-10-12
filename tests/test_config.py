import stat
import tempfile
import unittest
from pathlib import Path

from lg_ac.config import Config, load_config, save_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sub" / "config.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_gives_defaults(self):
        config = load_config(self.path)
        self.assertFalse(config.is_complete)
        self.assertTrue(config.client_id)

    def test_round_trip_is_private(self):
        save_config(Config(access_token="secret", country_code="BR", device_id="abc"), self.path)
        loaded = load_config(self.path)
        self.assertEqual((loaded.access_token, loaded.country_code, loaded.device_id), ("secret", "BR", "abc"))
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_client_id_is_stable(self):
        save_config(Config(), self.path)
        self.assertEqual(load_config(self.path).client_id, load_config(self.path).client_id)


if __name__ == "__main__":
    unittest.main()
