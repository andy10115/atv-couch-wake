from __future__ import annotations

import unittest

from atv_couch_wake.diagnostics import likely_controllers, parse_input_devices

SAMPLE = """I: Bus=0003 Vendor=0000 Product=0000 Version=0000
N: Name="GameSir Cyclone 2"
P: Phys=usb-0000:12:00.4-2/input0
H: Handlers=event20 js0

I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
H: Handlers=kbd event0
"""


class DiagnosticsTests(unittest.TestCase):
    def test_controller_filter(self) -> None:
        parsed = parse_input_devices(SAMPLE)
        controllers = likely_controllers(parsed)
        self.assertEqual(len(controllers), 1)
        self.assertEqual(controllers[0].name, "GameSir Cyclone 2")
        self.assertIn("12:00.4", controllers[0].phys)


if __name__ == "__main__":
    unittest.main()
