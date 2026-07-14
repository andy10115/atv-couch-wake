from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from atv_couch_wake.adb_control import TVControlError
from atv_couch_wake.config import AppConfig
from atv_couch_wake.controller_wake import ControllerWakeError, WakeConfigurationResult
from atv_couch_wake.diagnostics import ControllerWakePath
from atv_couch_wake.paths import AppPaths
from atv_couch_wake.platform_info import PlatformInfo
from atv_couch_wake.setup_wizard import (
    _choose_input,
    _test_power,
    run_controller_wake_setup,
    run_setup,
)
from atv_couch_wake.setup_wizard import (
    test_controller_wake as run_controller_wake_test,
)
from atv_couch_wake.ui import UI


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


class FakeUI(UI):
    name = "test"

    def __init__(self) -> None:
        self.infos: list[tuple[str, str]] = []
        self.errors: list[tuple[str, str]] = []

    def info(self, title: str, message: str) -> None:
        self.infos.append((title, message))

    def error(self, title: str, message: str) -> None:
        self.errors.append((title, message))

    def confirm(self, title: str, message: str, *, default: bool = True) -> bool:
        return default

    def prompt(self, title: str, message: str, *, default: str = "") -> str:
        if default:
            return default
        if title == "TV address":
            return "10.0.0.42"
        return "Test TV"

    def choose(self, title: str, message: str, choices: list[str]) -> int:
        return 0


class FailingPowerController:
    async def set_power(self, target_on: bool):
        raise TVControlError("test failure")


class ModelOnlyController:
    async def model(self) -> str:
        return "Test Model"


class SetupWizardTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_power_test_does_not_raise_or_stop_setup_flow(self) -> None:
        ui = FakeUI()
        off, on = await _test_power(ui, FailingPowerController())
        self.assertFalse(off)
        self.assertIsNone(on)
        self.assertTrue(any(title == "Power-off test failed" for title, _ in ui.errors))

    async def test_failed_input_discovery_preserves_existing_input(self) -> None:
        ui = FakeUI()
        config = AppConfig()
        config.tv.input_id = "vendor/.Input/HW15"
        config.tv.input_uri = "content://android.media.tv/passthrough/vendor%2F.Input%2FHW15"
        config.tv.input_label = "Gaming PC"
        controller = Mock()
        controller.discover_inputs = AsyncMock(side_effect=TVControlError("unavailable"))
        configured = await _choose_input(ui, controller, config)
        self.assertTrue(configured)
        self.assertEqual(config.tv.input_label, "Gaming PC")
        self.assertTrue(config.tv.input_uri)

    def test_controller_setup_configures_without_suspend_test(self) -> None:
        ui = FakeUI()
        config = AppConfig()
        path = ControllerWakePath(
            name="Gamepad Dongle",
            phys="usb-0000:12:00.4-2/input0",
            event="event20",
            usb_device="3-2",
            usb_root="usb3",
            usb_root_wakeup="disabled",
            pci_controller="0000:12:00.4",
            pci_wakeup="enabled",
            root_armed=False,
        )
        result = WakeConfigurationResult(
            "/etc/udev/rules.d/90-atv-couch-wake-controller.rules",
            ("usb3",),
            ("0000:12:00.4",),
            "selective",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = temp_paths(Path(directory))
            with (
                patch("atv_couch_wake.setup_wizard.configurable_paths", return_value=[path]),
                patch("atv_couch_wake.setup_wizard._controller_settle_delay", return_value=2.0),
                patch("atv_couch_wake.setup_wizard.install_wake_configuration", return_value=result),
                patch("atv_couch_wake.setup_wizard.subprocess.run") as run,
            ):
                configured = run_controller_wake_setup(ui, config, paths)
        self.assertTrue(configured)
        self.assertTrue(config.controller_wake.enabled)
        run.assert_not_called()

    def test_controller_suspend_test_refuses_same_boot(self) -> None:
        ui = FakeUI()
        config = AppConfig()
        config.controller_wake.enabled = True
        with (
            patch("atv_couch_wake.setup_wizard.controller_wake_reboot_required", return_value=True),
            patch("atv_couch_wake.setup_wizard.subprocess.run") as run,
            self.assertRaises(ControllerWakeError),
        ):
            run_controller_wake_test(ui, config)
        run.assert_not_called()

    async def test_optional_failures_do_not_prevent_user_service_install(self) -> None:
        ui = FakeUI()
        platform = PlatformInfo(
            distribution="Test Linux",
            version="1",
            variant="",
            kernel="test",
            atomic=False,
            systemd=True,
            user_systemd=True,
            python_version="3.13",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = temp_paths(Path(directory))
            with (
                patch("atv_couch_wake.setup_wizard.find_adb", return_value="/usr/bin/adb"),
                patch("atv_couch_wake.setup_wizard.inspect_platform", return_value=platform),
                patch("atv_couch_wake.setup_wizard.ADBController", return_value=ModelOnlyController()),
                patch("atv_couch_wake.setup_wizard._optional_wireless_pair", new=AsyncMock()),
                patch("atv_couch_wake.setup_wizard._authorize", new=AsyncMock()),
                patch("atv_couch_wake.setup_wizard._test_power", new=AsyncMock(return_value=(False, False))),
                patch("atv_couch_wake.setup_wizard._choose_input", new=AsyncMock(return_value=False)),
                patch("atv_couch_wake.setup_wizard.collect_diagnostics", new=AsyncMock(return_value=Mock())),
                patch("atv_couch_wake.setup_wizard.render_controller_wake", return_value="none"),
                patch("atv_couch_wake.setup_wizard.run_controller_wake_setup", return_value=False),
                patch(
                    "atv_couch_wake.setup_wizard.install_user_service",
                    return_value=paths.user_unit_file,
                ) as install,
            ):
                await run_setup(ui, paths=paths)
        install.assert_called_once()
        self.assertTrue(any(title == "Automation installed" for title, _ in ui.infos))


if __name__ == "__main__":
    unittest.main()
