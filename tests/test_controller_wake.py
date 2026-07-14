from __future__ import annotations

import unittest
from unittest import mock

from atv_couch_wake.config import AppConfig
from atv_couch_wake.controller_wake import (
    configurable_paths,
    controller_wake_reboot_required,
    render_all_roots_rule,
    render_selective_rule,
    save_all_roots,
    save_selected_path,
)
from atv_couch_wake.diagnostics import ControllerWakePath


class ControllerWakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ControllerWakePath(
            name="8BitDo USB Adapter 2",
            phys="usb-0000:12:00.4-2/input0",
            event="event20",
            usb_device="3-2",
            usb_root="usb3",
            usb_root_wakeup="disabled",
            pci_controller="0000:12:00.4",
            pci_wakeup="enabled",
            root_armed=False,
        )

    def test_configurable_paths_deduplicates_duplicate_input_interfaces(self) -> None:
        duplicate = ControllerWakePath(**self.path.__dict__)
        paths = configurable_paths([self.path, duplicate])
        self.assertEqual(len(paths), 1)

    def test_selective_rule_targets_stable_root_and_pci(self) -> None:
        rule = render_selective_rule(self.path)
        self.assertIn('KERNEL=="usb3"', rule)
        self.assertIn('KERNELS=="0000:12:00.4"', rule)
        self.assertIn('KERNEL=="0000:12:00.4"', rule)
        self.assertNotIn("event20", rule)
        self.assertNotIn("3-2", rule)

    def test_all_roots_rule_is_explicitly_broad(self) -> None:
        rule = render_all_roots_rule()
        self.assertIn('KERNEL=="usb*"', rule)
        self.assertIn('ATTR{power/wakeup}="enabled"', rule)

    def test_save_selected_path(self) -> None:
        config = AppConfig()
        save_selected_path(config, self.path, settle_delay_seconds=2.0)
        self.assertTrue(config.controller_wake.enabled)
        self.assertEqual(config.controller_wake.controller_name, "8BitDo USB Adapter 2")
        self.assertEqual(config.controller_wake.usb_root, "usb3")
        self.assertEqual(config.controller_wake.pci_controller, "0000:12:00.4")
        self.assertEqual(config.controller_wake.mode, "selective")
        self.assertFalse(config.controller_wake.verified)
        self.assertEqual(config.controller_wake.settle_delay_seconds, 2.0)

    def test_reboot_required_only_during_configuring_boot(self) -> None:
        config = AppConfig()
        config.controller_wake.enabled = True
        config.controller_wake.configured_boot_id = "boot-a"
        with mock.patch("atv_couch_wake.controller_wake.current_boot_id", return_value="boot-a"):
            self.assertTrue(controller_wake_reboot_required(config))
        with mock.patch("atv_couch_wake.controller_wake.current_boot_id", return_value="boot-b"):
            self.assertFalse(controller_wake_reboot_required(config))

    def test_save_all_roots(self) -> None:
        config = AppConfig()
        save_all_roots(config, settle_delay_seconds=1.5)
        self.assertTrue(config.controller_wake.enabled)
        self.assertEqual(config.controller_wake.mode, "all-roots")
        self.assertEqual(config.controller_wake.usb_root, "*")
        self.assertEqual(config.controller_wake.settle_delay_seconds, 1.5)


if __name__ == "__main__":
    unittest.main()
