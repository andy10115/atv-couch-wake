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
            config.tv.model = "TCL QM6K"
            config.tv.adb_path = "/usr/bin/adb"
            config.tv.input_id = "com.tcl.tvinput/.TvPassThroughService/HW15"
            config.tv.input_uri = (
                "content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2FHW15"
            )
            config.tv.input_label = "HDMI 3"
            config.behavior.off_on_reboot = True

            save_config(config, paths)
            loaded = load_config(paths)

            self.assertEqual(loaded.tv.host, "10.0.0.42")
            self.assertEqual(loaded.tv.name, 'Living Room "TCL"')
            self.assertEqual(loaded.tv.input_label, "HDMI 3")
            self.assertEqual(loaded.tv.serial, "10.0.0.42:5555")
            self.assertTrue(loaded.behavior.off_on_reboot)
            self.assertEqual(paths.config_file.stat().st_mode & 0o777, 0o600)

    def test_missing_optional_config_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(temp_paths(Path(directory)), required=False)
            self.assertEqual(config.tv.port, 5555)
            self.assertFalse(config.behavior.off_on_reboot)


if __name__ == "__main__":
    unittest.main()


class LegacyConfigTests(unittest.TestCase):
    def test_legacy_remote_fields_are_ignored(self) -> None:
        legacy = {
            "tv": {
                "host": "10.0.0.42",
                "name": "Old TV",
                "api_port": 6466,
                "pair_port": 6467,
                "hdmi_input": 3,
                "client_name": "atv-couch-wake",
            },
            "behavior": {
                "on_resume": True,
                "command_ready_delay_seconds": 1.0,
                "power_attempts": 6,
            },
            "discovery": {"mdns": True},
        }
        config = AppConfig.from_dict(legacy)
        self.assertEqual(config.tv.host, "10.0.0.42")
        self.assertEqual(config.tv.name, "Old TV")
        self.assertEqual(config.tv.port, 5555)
        self.assertEqual(config.tv.input_uri, "")
        self.assertTrue(config.behavior.on_resume)
