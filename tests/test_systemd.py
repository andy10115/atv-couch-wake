from __future__ import annotations

import unittest

from atv_couch_wake.systemd_integration import render_user_unit


class SystemdTests(unittest.TestCase):
    def test_unit_uses_supplied_python(self) -> None:
        unit = render_user_unit("/home/test/.local/share/atv-couch-wake/venv/bin/python")
        self.assertIn('ExecStart="/home/test/.local/share/atv-couch-wake/venv/bin/python"', unit)
        self.assertIn("-m atv_couch_wake watcher", unit)
        self.assertIn("Documentation=https://github.com/andy10115/atv-couch-wake", unit)
        self.assertIn("WantedBy=default.target", unit)


if __name__ == "__main__":
    unittest.main()
