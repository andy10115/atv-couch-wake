"""State-aware Android TV control."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

from .config import AppConfig, save_config
from .discovery import DeviceCandidate, discover_all
from .paths import AppPaths

LOGGER = logging.getLogger(__name__)

try:  # Keep imports optional so diagnostics/tests can still load without dependencies.
    from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth
except ImportError:  # pragma: no cover - only used in incomplete installations
    AndroidTVRemote = None  # type: ignore[assignment]

    class CannotConnect(Exception):
        pass

    class ConnectionClosed(Exception):
        pass

    class InvalidAuth(Exception):
        pass


class RemoteLike(Protocol):
    host: str

    @property
    def is_on(self) -> bool | None: ...

    @property
    def current_app(self) -> str | None: ...

    @property
    def device_info(self) -> Any: ...

    async def async_connect(self) -> None: ...

    async def async_generate_cert_if_missing(self) -> bool: ...

    async def async_get_name_and_mac(self) -> tuple[str, str]: ...

    async def async_start_pairing(self) -> None: ...

    async def async_finish_pairing(self, pairing_code: str) -> None: ...

    def add_is_on_updated_callback(self, callback: Callable[[bool], None]) -> None: ...

    def remove_is_on_updated_callback(self, callback: Callable[[bool], None]) -> None: ...

    def send_key_command(self, key_code: int | str, direction: int | str = "SHORT") -> None: ...

    def disconnect(self) -> None: ...


RemoteFactory = Callable[[str, str, str, str, int, int], RemoteLike]


class TVControlError(RuntimeError):
    """Base error for user-facing TV control failures."""


class PairingRequired(TVControlError):
    """Raised when the TV no longer accepts the saved certificate."""


@dataclass(frozen=True)
class PowerResult:
    target_on: bool
    success: bool
    verified: bool
    attempts: int
    message: str


@dataclass(frozen=True)
class TVStatus:
    host: str
    is_on: bool | None
    current_app: str | None
    device_info: Any


def _default_remote_factory(
    client_name: str,
    cert_file: str,
    key_file: str,
    host: str,
    api_port: int,
    pair_port: int,
) -> RemoteLike:
    if AndroidTVRemote is None:
        raise TVControlError("androidtvremote2 is not installed. Re-run install.sh.")
    return AndroidTVRemote(
        client_name,
        cert_file,
        key_file,
        host,
        api_port=api_port,
        pair_port=pair_port,
        enable_voice=False,
    )


class TVController:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths | None = None,
        *,
        remote_factory: RemoteFactory = _default_remote_factory,
        discoverer: Callable[..., Any] = discover_all,
    ) -> None:
        self.config = config
        self.paths = paths or AppPaths.from_environment()
        self.remote_factory = remote_factory
        self.discoverer = discoverer

    def _remote(self, host: str | None = None) -> RemoteLike:
        tv = self.config.tv
        return self.remote_factory(
            tv.client_name,
            str(self.paths.cert_file),
            str(self.paths.key_file),
            host or tv.host,
            tv.api_port,
            tv.pair_port,
        )

    def _ensure_ready(self) -> None:
        if not self.config.tv.host:
            raise TVControlError("No TV host is configured. Run 'atv-couch-wake setup'.")
        if not self.paths.cert_file.exists() or not self.paths.key_file.exists():
            raise PairingRequired("Pairing certificate is missing. Run 'atv-couch-wake pair'.")

    async def _connect_host(self, host: str) -> RemoteLike:
        remote = self._remote(host)
        try:
            await asyncio.wait_for(
                remote.async_connect(), timeout=self.config.behavior.connect_timeout_seconds
            )
            return remote
        except asyncio.TimeoutError as exc:
            remote.disconnect()
            raise CannotConnect(f"Timed out connecting to {host}") from exc

    async def _candidate_matches(self, candidate: DeviceCandidate) -> bool:
        remote = self._remote(candidate.host)
        try:
            name, mac = await asyncio.wait_for(
                remote.async_get_name_and_mac(), timeout=self.config.behavior.connect_timeout_seconds
            )
        except (CannotConnect, ConnectionClosed, InvalidAuth, asyncio.TimeoutError, OSError, ValueError):
            return False
        finally:
            remote.disconnect()

        expected_mac = self.config.tv.mac.casefold().strip()
        if expected_mac and mac.casefold().strip() == expected_mac:
            return True
        expected_name = self.config.tv.name.casefold().strip()
        return bool(expected_name and name.casefold().strip() == expected_name)

    async def _rediscover(self) -> str | None:
        d = self.config.discovery
        candidates = await self.discoverer(
            mdns=d.mdns,
            subnet_scan=d.subnet_scan,
            mdns_timeout=d.mdns_timeout_seconds,
            probe_timeout=d.probe_timeout_seconds,
            configured_subnet=d.subnet,
        )
        if not candidates:
            return None

        for candidate in candidates:
            if await self._candidate_matches(candidate):
                return candidate.host

        # A first-run/manual config may not have identity data. Only accept an unambiguous result.
        if not self.config.tv.mac and not self.config.tv.name and len(candidates) == 1:
            return candidates[0].host
        return None

    async def connect(self) -> RemoteLike:
        self._ensure_ready()
        host = self.config.tv.host
        try:
            return await self._connect_host(host)
        except InvalidAuth as exc:
            raise PairingRequired("Saved pairing is no longer accepted. Run 'atv-couch-wake pair'.") from exc
        except (CannotConnect, ConnectionClosed, OSError) as first_error:
            LOGGER.warning("Could not connect to %s; attempting rediscovery", host)
            new_host = await self._rediscover()
            if not new_host or new_host == host:
                raise TVControlError(f"Could not connect to TV at {host}: {first_error}") from first_error
            self.config.tv.host = new_host
            save_config(self.config, self.paths)
            LOGGER.info("Rediscovered TV at %s and updated configuration", new_host)
            try:
                return await self._connect_host(new_host)
            except InvalidAuth as exc:
                raise PairingRequired("TV found, but pairing is no longer accepted.") from exc
            except (CannotConnect, ConnectionClosed, OSError) as exc:
                raise TVControlError(f"TV was found at {new_host}, but connection failed: {exc}") from exc

    async def _wait_for_state(
        self,
        remote: RemoteLike,
        *,
        target: bool | None = None,
        timeout: float | None = None,
    ) -> bool | None:
        current = remote.is_on
        if current is not None and (target is None or current == target):
            return current

        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def updated(value: bool) -> None:
            if not future.done() and (target is None or value == target):
                future.set_result(value)

        remote.add_is_on_updated_callback(updated)
        try:
            return await asyncio.wait_for(
                future,
                timeout=timeout or self.config.behavior.state_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return remote.is_on
        finally:
            with suppress(ValueError):
                remote.remove_is_on_updated_callback(updated)

    async def status(self) -> TVStatus:
        remote = await self.connect()
        try:
            state = await self._wait_for_state(remote)
            return TVStatus(
                host=remote.host,
                is_on=state,
                current_app=remote.current_app,
                device_info=remote.device_info,
            )
        finally:
            remote.disconnect()

    async def _verify_power_after_disconnect(self, target_on: bool) -> bool:
        """Reconnect briefly after a command-triggered disconnect and verify state."""
        for _ in range(2):
            await asyncio.sleep(1.0)
            try:
                remote = await self.connect()
            except TVControlError:
                continue
            try:
                state = await self._wait_for_state(
                    remote,
                    target=target_on,
                    timeout=self.config.behavior.state_timeout_seconds,
                )
                if state == target_on:
                    return True
            finally:
                remote.disconnect()
        return False

    async def set_power(self, target_on: bool) -> PowerResult:
        remote = await self.connect()
        behavior = self.config.behavior
        target_name = "on" if target_on else "off"
        attempts = 0
        try:
            state = await self._wait_for_state(remote)
            if state == target_on:
                return PowerResult(target_on, True, True, attempts, f"TV is already {target_name}.")

            # Android TV Remote commands can be silently dropped immediately after connect.
            # The reference implementation waits one second before its first command.
            await asyncio.sleep(max(0.0, behavior.command_ready_delay_seconds))

            # POWER is a toggle, but it is safe when the current state is known and
            # opposite to the target. Re-check before every retry so we never overshoot.
            if state is not None:
                for _ in range(max(1, behavior.power_attempts)):
                    current = remote.is_on
                    if current == target_on:
                        return PowerResult(target_on, True, True, attempts, f"TV turned {target_name}.")
                    if current is None:
                        break
                    remote.send_key_command("POWER")
                    attempts += 1
                    state = await self._wait_for_state(
                        remote,
                        target=target_on,
                        timeout=behavior.command_settle_seconds,
                    )
                    if state == target_on:
                        return PowerResult(target_on, True, True, attempts, f"TV turned {target_name}.")

            # If state is unknown, never send a blind toggle. Try the discrete command
            # once; some firmware supports it even though others (notably some TCL sets)
            # ignore it.
            if remote.is_on is None:
                discrete_key = "WAKEUP" if target_on else "SLEEP"
                remote.send_key_command(discrete_key)
                attempts += 1
                state = await self._wait_for_state(
                    remote,
                    target=target_on,
                    timeout=behavior.command_settle_seconds,
                )
                if state == target_on:
                    return PowerResult(target_on, True, True, attempts, f"TV turned {target_name}.")
                return PowerResult(
                    target_on,
                    False,
                    False,
                    attempts,
                    f"Sent {discrete_key}, but the TV did not report the requested {target_name} state. "
                    "A blind POWER toggle was not sent because the current state is unknown.",
                )

            return PowerResult(
                target_on,
                False,
                False,
                attempts,
                f"TV did not reach the requested {target_name} state after {attempts} POWER send(s).",
            )
        except ConnectionClosed:
            if attempts and await self._verify_power_after_disconnect(target_on):
                return PowerResult(
                    target_on,
                    True,
                    True,
                    attempts,
                    f"TV turned {target_name}; state was verified after reconnecting.",
                )
            if not target_on and attempts:
                return PowerResult(
                    target_on,
                    True,
                    False,
                    attempts,
                    "TV connection closed after the power command; power-off is likely but unverified.",
                )
            return PowerResult(
                target_on,
                False,
                False,
                attempts,
                "The TV connection closed during power-on and the requested state could not be verified.",
            )
        finally:
            remote.disconnect()

    async def send_key(self, key_code: str, *, settle_seconds: float = 0.75) -> None:
        """Send one raw Android TV key after the post-connect readiness delay."""
        remote = await self.connect()
        try:
            await self._wait_for_state(remote)
            await asyncio.sleep(max(0.0, self.config.behavior.command_ready_delay_seconds))
            remote.send_key_command(key_code)
            await asyncio.sleep(max(0.0, settle_seconds))
        finally:
            remote.disconnect()

    async def select_input(self, hdmi_input: int | None = None) -> None:
        selected = self.config.tv.hdmi_input if hdmi_input is None else hdmi_input
        if selected not in {1, 2, 3, 4}:
            raise TVControlError("HDMI input must be 1, 2, 3, or 4.")
        await self.send_key(f"TV_INPUT_HDMI_{selected}")

    async def wake_and_select_input(self) -> PowerResult:
        result = await self.set_power(True)
        if not result.success:
            return result
        if self.config.behavior.switch_input_after_wake and self.config.tv.hdmi_input:
            await asyncio.sleep(self.config.behavior.wake_settle_seconds)
            await self.select_input()
        return result
