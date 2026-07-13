"""Guided Android TV pairing."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass

from .config import AppConfig
from .paths import AppPaths
from .remote import (
    CannotConnect,
    ConnectionClosed,
    InvalidAuth,
    RemoteFactory,
    TVControlError,
    _default_remote_factory,
)
from .ui import UI


@dataclass(frozen=True)
class PairedDevice:
    host: str
    name: str
    mac: str


async def pair_device(
    host: str,
    config: AppConfig,
    ui: UI,
    paths: AppPaths | None = None,
    *,
    remote_factory: RemoteFactory = _default_remote_factory,
) -> PairedDevice:
    paths = paths or AppPaths.from_environment()
    paths.ensure_private_directories()
    tv = config.tv
    remote = remote_factory(
        tv.client_name,
        str(paths.cert_file),
        str(paths.key_file),
        host,
        tv.api_port,
        tv.pair_port,
    )
    try:
        await remote.async_generate_cert_if_missing()
        name, mac = await asyncio.wait_for(
            remote.async_get_name_and_mac(), timeout=config.behavior.connect_timeout_seconds
        )
        ui.info(
            "TV found",
            f"Found {name} at {host}. A pairing code will appear on the TV after you continue.",
        )
        await remote.async_start_pairing()
        while True:
            code = ui.prompt("Pair Google TV", "Enter the pairing code shown on the television")
            try:
                await remote.async_finish_pairing(code.strip())
                break
            except InvalidAuth:
                ui.error("Pairing failed", "That code was rejected. Enter the new code shown on the TV.")
                await remote.async_start_pairing()
        for file in (paths.cert_file, paths.key_file):
            with suppress(OSError):
                file.chmod(0o600)
        return PairedDevice(host=host, name=name, mac=mac)
    except asyncio.TimeoutError as exc:
        raise TVControlError(f"Timed out contacting the TV at {host}.") from exc
    except CannotConnect as exc:
        raise TVControlError(f"Could not reach Android TV pairing service at {host}:{tv.pair_port}.") from exc
    except ConnectionClosed as exc:
        raise TVControlError("The TV closed the pairing request. Start pairing again.") from exc
    finally:
        remote.disconnect()
