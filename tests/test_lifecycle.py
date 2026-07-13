from __future__ import annotations

import unittest

from atv_couch_wake.config import AppConfig
from atv_couch_wake.lifecycle import _metadata_shutdown_type, _wake_with_retries
from atv_couch_wake.remote import PowerResult, TVControlError


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


if __name__ == "__main__":
    unittest.main()
