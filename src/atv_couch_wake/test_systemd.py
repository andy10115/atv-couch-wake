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

    def test_unit_orders_after_network_online(self) -> None:
        """
        Regression test: without After=/Wants=network-online.target, the
        watcher can start (via default.target on login) before the network
        interface has associated/gotten a DHCP lease. On boot this raced
        the fixed startup delay and could cause the very first adb connect
        attempts to fail before the network was actually usable.
        """
        unit = render_user_unit()
        self.assertIn("After=network-online.target", unit)
        self.assertIn("Wants=network-online.target", unit)


if __name__ == "__main__":
    unittest.main()
