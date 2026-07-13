from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atv_couch_wake.diagnostics import (
    controller_wake_paths,
    likely_controllers,
    parse_input_devices,
)

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

    def test_controller_maps_to_enabled_usb_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            physical = root / "devices/pci0000:00/0000:12:00.4/usb3/3-2/3-2:1.0/input/input20"
            physical.mkdir(parents=True)
            pci = root / "devices/pci0000:00/0000:12:00.4"
            usb_root = pci / "usb3"
            (pci / "power").mkdir()
            (pci / "power/wakeup").write_text("enabled\n", encoding="utf-8")
            (usb_root / "power").mkdir()
            (usb_root / "power/wakeup").write_text("enabled\n", encoding="utf-8")

            input_base = root / "class/input"
            (input_base / "event20").mkdir(parents=True)
            (input_base / "event20/device").symlink_to(physical, target_is_directory=True)

            usb_base = root / "bus/usb/devices"
            usb_base.mkdir(parents=True)
            (usb_base / "usb3").symlink_to(usb_root, target_is_directory=True)
            pci_base = root / "bus/pci/devices"
            pci_base.mkdir(parents=True)
            (pci_base / "0000:12:00.4").symlink_to(pci, target_is_directory=True)

            result = controller_wake_paths(
                parse_input_devices(SAMPLE),
                input_base=input_base,
                usb_base=usb_base,
                pci_base=pci_base,
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].usb_device, "3-2")
            self.assertEqual(result[0].usb_root, "usb3")
            self.assertEqual(result[0].usb_root_wakeup, "enabled")
            self.assertEqual(result[0].pci_controller, "0000:12:00.4")
            self.assertEqual(result[0].pci_wakeup, "enabled")
            self.assertTrue(result[0].root_armed)


if __name__ == "__main__":
    unittest.main()
