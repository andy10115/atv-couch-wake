"""Guided ADB-first setup wizard."""

from __future__ import annotations

import asyncio
import ipaddress

from .adb_control import (
    ADBController,
    ADBNotInstalled,
    ADBUnauthorized,
    TVControlError,
    TVInput,
    find_adb,
    installation_help,
)
from .config import AppConfig, load_config, save_config
from .diagnostics import collect_diagnostics, render_controller_wake
from .paths import AppPaths
from .platform_info import inspect_platform
from .systemd_integration import install_user_service, shell_command_for_logs
from .ui import UI

DEVELOPER_MODE_GUIDE = """Before continuing, configure the TV:

1. Open Settings → System → About.
2. Highlight “Android TV OS build” and press OK/Select seven times.
3. Go back to System → Developer options.
4. Enable USB debugging, Network debugging, or Wireless debugging—use the option your TV exposes.
5. Accept the warning.

The exact labels vary by manufacturer. This app uses ADB over your trusted local network."""


POWER_GUIDE = """Keep the TV reachable while its panel is off:

1. Open Settings → System → Power & Energy (sometimes just “Power”).
2. Set Energy mode / Energy Saver to “Optimized” when that option exists.
3. Enable Quick Start, Quick Resume, Fast TV Start, or Network Standby—whichever name your TV uses.
4. Avoid an aggressive Eco/Low-power mode that fully disables networking in standby.

These settings are required for reliable ADB wake after the TV sleeps."""


def _validate_host(value: str) -> str:
    value = value.strip()
    if not value:
        raise TVControlError("A TV address is required.")
    if ":" in value:
        host, _, port = value.rpartition(":")
        try:
            ipaddress.ip_address(host)
            int(port)
        except ValueError as exc:
            raise TVControlError("Enter an IPv4 address such as 10.0.0.42 or 10.0.0.42:5555.") from exc
        return value
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise TVControlError("Enter an IPv4 address such as 10.0.0.42.") from exc
    return value


def _set_address(config: AppConfig, entered: str) -> None:
    if ":" in entered:
        host, _, port = entered.rpartition(":")
        config.tv.host = host
        config.tv.port = int(port)
    else:
        config.tv.host = entered


async def _authorize(ui: UI, controller: ADBController) -> None:
    ui.info(
        "Authorize this computer",
        "A debugging authorization prompt should appear on the TV. Choose “Always allow from this "
        "computer,” then accept it.\n\nThe wizard will keep checking for up to one minute.",
    )
    try:
        await controller.connect()
    except TVControlError as exc:
        ui.error(
            "ADB connection failed",
            f"{exc}\n\nConfirm debugging is enabled, the TV and PC are on the same network, "
            "and the address/port are correct.",
        )
        raise

    for _ in range(30):
        state = await controller.device_state()
        if state == "device":
            return
        await asyncio.sleep(2.0)
    raise ADBUnauthorized(
        "The TV never authorized this computer. Revoke debugging authorizations on the TV if needed, "
        "then rerun setup and accept the prompt."
    )


async def _optional_wireless_pair(ui: UI, controller: ADBController) -> None:
    if not ui.confirm(
        "Wireless debugging pairing",
        "Does the TV require a pairing code and show a separate pairing address/port?",
        default=False,
    ):
        return
    address = ui.prompt(
        "Pairing address",
        "Enter the pairing address shown by the TV, including its port (for example 10.0.0.42:37123)",
    )
    code = ui.prompt("Pairing code", "Enter the six-digit pairing code shown by the TV")
    await controller.pair(address, code)
    ui.info("Wireless pairing complete", "The PC paired successfully. Continuing with the ADB connection.")


async def _test_power(ui: UI, controller: ADBController) -> None:
    if not ui.confirm(
        "Test power control",
        "Test ADB sleep and wake now? Keep the physical remote nearby during this first test.",
        default=True,
    ):
        return

    ui.info("Power-off test", "Sending KEYCODE_SLEEP. The panel should turn off.")
    off = await controller.set_power(False)
    if not off.success:
        raise TVControlError(off.message)
    if not ui.confirm("Power-off result", "Did the TV turn off?", default=True):
        raise TVControlError("The TV did not confirm the ADB sleep test.")

    ui.info("Power-on test", "Waiting five seconds, then sending KEYCODE_WAKEUP.")
    await asyncio.sleep(5.0)
    on = await controller.set_power(True)
    if not on.success:
        raise TVControlError(on.message)
    if not ui.confirm("Power-on result", "Did the TV turn back on?", default=True):
        raise TVControlError("The TV did not confirm the ADB wake test.")


async def _choose_input(ui: UI, controller: ADBController, config: AppConfig) -> None:
    inputs = await controller.discover_inputs()
    if not inputs:
        ui.error(
            "No passthrough inputs found",
            "The TV did not expose any physical passthrough inputs through Android's TV Input "
            "Framework. Power automation can still be configured.",
        )
        config.tv.input_id = ""
        config.tv.input_uri = ""
        config.tv.input_label = ""
        config.behavior.switch_input_after_wake = False
        return

    ui.info(
        "Input test",
        "Use the physical remote to open the Google TV Home screen or switch away from the gaming PC.\n\n"
        "The wizard will now launch each passthrough input directly. Answer Yes when the PC's input appears.",
    )

    selected: TVInput | None = None
    for index, candidate in enumerate(inputs, start=1):
        if not ui.confirm(
            "Test input",
            f"Test {candidate.hardware_id} ({index} of {len(inputs)})?",
            default=True,
        ):
            continue
        await controller.select_input(candidate.uri)
        if ui.confirm(
            "Input result",
            f"Did {candidate.hardware_id} switch the TV to this gaming PC?",
            default=False,
        ):
            selected = candidate
            break

    if selected is None:
        ui.info(
            "Input switching skipped",
            "No input was confirmed. Wake and sleep automation can still be enabled, and you can rerun "
            "'atv-couch-wake test inputs' later.",
        )
        config.tv.input_id = ""
        config.tv.input_uri = ""
        config.tv.input_label = ""
        config.behavior.switch_input_after_wake = False
        return

    label = ui.prompt(
        "Input label",
        "Give this input a friendly label",
        default=selected.hardware_id,
    )
    config.tv.input_id = selected.input_id
    config.tv.input_uri = selected.uri
    config.tv.input_label = label
    config.behavior.switch_input_after_wake = True
    ui.info("Input saved", f"Saved {label}:\n{selected.input_id}")


async def run_setup(
    ui: UI,
    *,
    host_override: str = "",
    install_service: bool = True,
    paths: AppPaths | None = None,
) -> AppConfig:
    paths = paths or AppPaths.from_environment()
    config = load_config(paths, required=False)
    platform = inspect_platform()

    try:
        config.tv.adb_path = find_adb(config.tv.adb_path)
    except ADBNotInstalled:
        ui.error("ADB is required", installation_help())
        raise

    ui.info(
        "Welcome to atv-couch-wake",
        "This setup uses Android Debug Bridge (ADB) for reliable TV wake, sleep, and direct input "
        "selection. It does not install system packages or modify the immutable OS image.\n\n"
        f"Detected: {platform.distribution}\nADB: {config.tv.adb_path}",
    )
    ui.info("Enable developer options", DEVELOPER_MODE_GUIDE)
    if not ui.confirm("Developer options", "Have you enabled debugging on the TV?", default=True):
        raise TVControlError("Enable TV debugging, then rerun setup.")

    ui.info("Configure standby networking", POWER_GUIDE)
    if not ui.confirm(
        "Power settings",
        "Have you selected Optimized energy mode and/or enabled Quick Start / Quick Resume?",
        default=True,
    ):
        raise TVControlError("Configure the TV's power settings, then rerun setup.")

    default_address = config.tv.serial or config.tv.host
    address = host_override or ui.prompt(
        "TV address",
        "Enter the TV's IP address. Include a port only when it is not 5555",
        default=default_address,
    )
    _set_address(config, _validate_host(address))
    controller = ADBController(config)

    await _optional_wireless_pair(ui, controller)
    await _authorize(ui, controller)

    config.tv.model = await controller.model()
    config.tv.name = ui.prompt(
        "TV name",
        "Give this television a friendly name",
        default=config.tv.name or config.tv.model or "Living Room TV",
    )
    save_config(config, paths)
    ui.info(
        "ADB connection verified",
        f"Authorized {config.tv.name} ({config.tv.model or 'unknown model'}) at {config.tv.serial}.",
    )

    await _test_power(ui, controller)
    await _choose_input(ui, controller, config)

    config.behavior.on_startup = ui.confirm(
        "Startup behavior",
        "Wake the TV when this user's systemd session starts?",
        default=config.behavior.on_startup,
    )
    config.behavior.on_resume = ui.confirm(
        "Resume behavior",
        "Wake the TV and select the saved PC input after resume?",
        default=config.behavior.on_resume,
    )
    config.behavior.off_on_suspend = ui.confirm(
        "Suspend behavior",
        "Put the TV to sleep before the PC suspends?",
        default=config.behavior.off_on_suspend,
    )
    config.behavior.off_on_shutdown = ui.confirm(
        "Shutdown behavior",
        "Put the TV to sleep before the PC shuts down?",
        default=config.behavior.off_on_shutdown,
    )
    config.behavior.off_on_reboot = ui.confirm(
        "Reboot behavior",
        "Also put the TV to sleep during a reboot? This is normally left disabled.",
        default=config.behavior.off_on_reboot,
    )
    save_config(config, paths)

    try:
        wake_report = await collect_diagnostics(paths)
        ui.info("Controller wake check", render_controller_wake(wake_report))
    except Exception as exc:  # Diagnostics must not block TV setup.
        ui.error("Controller wake check unavailable", str(exc))

    if install_service and not platform.user_systemd:
        ui.error(
            "User service unavailable",
            "A working per-user systemd manager was not detected. Manual ADB commands and TV control "
            "are configured, but lifecycle automation was not installed.",
        )
        install_service = False

    if install_service and ui.confirm(
        "Install automation",
        "Install and start the per-user systemd lifecycle watcher now?\n\n"
        "No system-wide service or root-owned hook will be created.",
        default=True,
    ):
        unit = install_user_service(paths)
        ui.info(
            "Setup complete",
            f"Installed user service:\n{unit}\n\nFollow its logs with:\n{shell_command_for_logs()}",
        )
        return config

    ui.info(
        "Setup complete",
        "Configuration was saved. Run 'atv-couch-wake service install' later to enable automation.",
    )
    return config
