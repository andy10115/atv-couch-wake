from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atv_couch_wake.config import AppConfig, load_config, save_config
from atv_couch_wake.paths import AppPaths


def temp_paths(root: Path) -> AppPaths:
    return AppPaths(
        config_dir=root / "config",
        data_dir=root / "data",
        state_dir=root / "state",
        runtime_dir=root / "runtime",
        config_file=root / "config/config.toml",
        cert_file=root / "data/cert.pem",
        key_file=root / "data/key.pem",
        user_unit_dir=root / "config/systemd/user",
        user_unit_file=root / "config/systemd/user/atv-couch-wake-watcher.service",
    )


class ConfigTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = temp_paths(Path(directory))
            config = AppConfig()
            config.tv.host = "10.0.0.42"
            config.tv.name = 'Living Room "TCL"'
            config.tv.hdmi_input = 3
            config.behavior.off_on_reboot = True
            config.discovery.subnet = "10.0.0.0/24"

            save_config(config, paths)
            loaded = load_config(paths)

            self.assertEqual(loaded.tv.host, "10.0.0.42")
            self.assertEqual(loaded.tv.name, 'Living Room "TCL"')
            self.assertEqual(loaded.tv.hdmi_input, 3)
            self.assertTrue(loaded.behavior.off_on_reboot)
            self.assertEqual(loaded.discovery.subnet, "10.0.0.0/24")
            self.assertEqual(paths.config_file.stat().st_mode & 0o777, 0o600)

    def test_missing_optional_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(temp_paths(Path(directory)), required=False)
            self.assertEqual(config.tv.api_port, 6466)
            self.assertFalse(config.behavior.off_on_reboot)


if __name__ == "__main__":
    unittest.main()
