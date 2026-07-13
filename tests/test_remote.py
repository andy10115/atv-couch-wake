from __future__ import annotations

import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from atv_couch_wake.config import AppConfig
from atv_couch_wake.paths import AppPaths
from atv_couch_wake.remote import TVController


class FakeRemote:
    def __init__(self, state: bool | None, *, react_to_discrete: bool = True) -> None:
        self.host = "10.0.0.42"
        self._state = state
        self.current_app = "test.app"
        self.device_info = "fake-tv"
        self.commands: list[str] = []
        self.callbacks: list[Callable[[bool], None]] = []
        self.react_to_discrete = react_to_discrete

    @property
    def is_on(self) -> bool | None:
        return self._state

    async def async_connect(self) -> None:
        return None

    async def async_generate_cert_if_missing(self) -> bool:
        return False

    async def async_get_name_and_mac(self) -> tuple[str, str]:
        return "Fake TV", "AA:BB:CC:DD:EE:FF"

    async def async_start_pairing(self) -> None:
        return None

    async def async_finish_pairing(self, pairing_code: str) -> None:
        return None

    def add_is_on_updated_callback(self, callback: Callable[[bool], None]) -> None:
        self.callbacks.append(callback)

    def remove_is_on_updated_callback(self, callback: Callable[[bool], None]) -> None:
        self.callbacks.remove(callback)

    def send_key_command(self, key_code: int | str, direction: int | str = "SHORT") -> None:
        key = str(key_code)
        self.commands.append(key)
        if key == "WAKEUP" and self.react_to_discrete:
            self._state = True
        elif key == "SLEEP" and self.react_to_discrete:
            self._state = False
        elif key == "POWER" and self._state is not None:
            self._state = not self._state
        if self._state is not None:
            for callback in list(self.callbacks):
                callback(self._state)

    def disconnect(self) -> None:
        return None


def make_paths(root: Path) -> AppPaths:
    paths = AppPaths(
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
    paths.ensure_private_directories()
    paths.cert_file.write_text("cert", encoding="utf-8")
    paths.key_file.write_text("key", encoding="utf-8")
    return paths


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_already_on_does_not_send_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRemote(True)
            config = AppConfig()
            config.tv.host = fake.host
            controller = TVController(
                config,
                make_paths(Path(directory)),
                remote_factory=lambda *_args: fake,
            )
            result = await controller.set_power(True)
            self.assertTrue(result.success)
            self.assertTrue(result.verified)
            self.assertEqual(fake.commands, [])

    async def test_discrete_wakeup_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRemote(False)
            config = AppConfig()
            config.tv.host = fake.host
            config.behavior.command_settle_seconds = 0.01
            controller = TVController(
                config,
                make_paths(Path(directory)),
                remote_factory=lambda *_args: fake,
            )
            result = await controller.set_power(True)
            self.assertTrue(result.success)
            self.assertEqual(fake.commands, ["WAKEUP"])

    async def test_unknown_state_never_uses_power_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRemote(None, react_to_discrete=False)
            config = AppConfig()
            config.tv.host = fake.host
            config.behavior.state_timeout_seconds = 0.01
            config.behavior.command_settle_seconds = 0.01
            controller = TVController(
                config,
                make_paths(Path(directory)),
                remote_factory=lambda *_args: fake,
            )
            result = await controller.set_power(False)
            self.assertTrue(result.success)
            self.assertFalse(result.verified)
            self.assertEqual(fake.commands, ["SLEEP"])
            self.assertNotIn("POWER", fake.commands)


if __name__ == "__main__":
    unittest.main()
