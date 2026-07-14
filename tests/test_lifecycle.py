from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from atv_couch_wake.adb_control import PowerResult, TVControlError
from atv_couch_wake.config import AppConfig
from atv_couch_wake.lifecycle import _metadata_shutdown_type, _wake_with_retries, handle_event


class Variant:
    def __init__(self, value: str) -> None:
        self.value = value


class FlakyController:
    def __init__(self) -> None:
        self.calls = 0

    async def wake_and_select_input(self) -> PowerResult:
        self.calls += 1
        if self.calls == 1:
            raise TVControlError("network not ready")
        return PowerResult(True, True, True, 1, "TV turned on.")


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def test_reboot_metadata(self) -> None:
        self.assertEqual(_metadata_shutdown_type([True, {"type": Variant("reboot")}]), "reboot")

    def test_poweroff_metadata(self) -> None:
        self.assertEqual(_metadata_shutdown_type([True, {"type": Variant("power-off")}]), "shutdown")

    def test_legacy_signal_defaults_to_shutdown(self) -> None:
        self.assertEqual(_metadata_shutdown_type([True]), "shutdown")

    async def test_wake_retries_after_network_failure(self) -> None:
        config = AppConfig()
        config.behavior.wake_attempts = 2
        config.behavior.wake_retry_seconds = 0.001
        controller = FlakyController()
        result = await _wake_with_retries("resume", controller, config)
        self.assertTrue(result.success)
        self.assertEqual(controller.calls, 2)

    async def test_resume_waits_for_configured_settle_delay(self) -> None:
        config = AppConfig()
        config.behavior.resume_delay_seconds = 5.0
        with (
            patch("atv_couch_wake.lifecycle.ADBController") as controller_cls,
            patch("atv_couch_wake.lifecycle.asyncio.sleep", new=AsyncMock()) as sleep,
            patch("atv_couch_wake.lifecycle._wake_with_retries", new=AsyncMock()) as wake,
        ):
            wake.return_value = __import__("atv_couch_wake.lifecycle", fromlist=["EventResult"]).EventResult(
                "resume", True, True, "ok"
            )
            result = await handle_event("resume", config)
        controller_cls.assert_called_once()
        sleep.assert_awaited_once_with(5.0)
        wake.assert_awaited_once()
        self.assertTrue(result.success)

    async def test_suspend_settle_delay_runs_even_when_tv_poweroff_is_disabled(self) -> None:
        config = AppConfig()
        config.behavior.off_on_suspend = False
        config.controller_wake.enabled = True
        config.controller_wake.settle_delay_seconds = 2.0
        with (
            patch("atv_couch_wake.lifecycle.ADBController", return_value=object()),
            patch("atv_couch_wake.lifecycle.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            result = await handle_event("suspend", config, settle_delay_override=0.25)
        sleep.assert_awaited_once_with(0.25)
        self.assertTrue(result.success)
        self.assertFalse(result.performed)


if __name__ == "__main__":
    unittest.main()
