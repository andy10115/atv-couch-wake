"""Guided ADB-first setup wizard."""

from __future__ import annotations

import asyncio
import ipaddress
import subprocess

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
from .controller_wake import (
    ControllerWakeError,
    configurable_paths,
    install_wake_configuration,
    save_all_roots,
    save_selected_path,
    wol_fallback_summary,
)
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
        "Does the TV require a pairing code and show a separate pairing address/port? usually no",
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

    ui.info(
        "Power-off test", "Sending KEYCODE_SLEEP. The panel should turn off. (turn back on if successful)"
    )
    off = await controller.set_power(False)
    if not off.success:
        raise TVControlError(off.message)
    if not ui.confirm("Power-off result", "Did the TV turn off?", default=True):
        raise TVControlError("The TV did not confirm the ADB sleep test.")

    ui.info(
        "Power-on test", "Waiting five seconds, then sending KEYCODE_WAKEUP. (test begins on next prompt)"
    )
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
        "The wizard will now launch each passthrough input directly. Answer Yes when the PC's "
        "input appears. (Test begins on next prompt)",
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


def _controller_choice_label(path: object) -> str:
    return f"{path.name} — {path.usb_root} via {path.pci_controller} (root wake: {path.usb_root_wakeup})"


def _controller_settle_delay(ui: UI, config: AppConfig) -> float:
    if not ui.confirm(
        "Controller re-enumeration guard",
        "Some wireless dongles change USB identity when the controller connects or disconnects. "
        "That re-enumeration can itself cause an immediate unwanted wake if suspend begins at the "
        "same moment.\n\nEnable a short pre-suspend settling delay?",
        default=True,
    ):
        return 0.0
    default = str(config.controller_wake.settle_delay_seconds or 2.0)
    raw = ui.prompt(
        "Settle delay",
        "Seconds to wait before the PC enters suspend. The user-service watcher is limited by "
        "logind's delay-inhibitor window, so long values may be capped automatically",
        default=default,
    )
    try:
        value = float(raw)
    except ValueError as exc:
        raise TVControlError("The controller settle delay must be a number of seconds.") from exc
    return max(0.0, value)


def test_controller_wake(ui: UI, config: AppConfig) -> bool:
    ui.info(
        "Controller wake test",
        "The PC can now perform a real suspend test.\n\n"
        "1. Make sure you can still reach the PC's physical power button or a keyboard in case the "
        "controller cannot wake it.\n"
        "2. Turn the selected controller off before continuing.\n"
        "3. The PC will suspend.\n"
        "4. After it is fully asleep, turn the controller back on.\n\n"
        "If the controller cannot wake this hardware, use another available wake method to return "
        "to this setup session.",
    )
    if not ui.confirm("Run suspend test", "Suspend the PC now and test controller wake?", default=True):
        return False
    result = subprocess.run(["systemctl", "suspend"], check=False)
    if result.returncode != 0:
        raise ControllerWakeError("systemctl suspend failed; controller wake could not be tested.")
    return ui.confirm(
        "Wake test result",
        "Did turning on the selected controller wake the PC?",
        default=True,
    )


def run_controller_wake_setup(ui: UI, config: AppConfig, paths: AppPaths) -> None:
    if not ui.confirm(
        "Controller wake",
        "Would you like to try allowing a USB controller or wireless controller dongle to wake "
        "this PC from suspend?\n\nThis is optional and hardware-dependent.",
        default=True,
    ):
        return

    ui.info(
        "How controller wake works",
        "atv-couch-wake traces the selected controller to its USB root hub and, when available, "
        "its parent PCI USB controller. It enables wake on that stable hardware path rather than "
        "the temporary controller device itself.\n\nThis matters for wireless dongles that re-enumerate or "
        "change device identity when the controller turns on or off.\n\nEnabling a USB root hub may also "
        "allow other wake-capable devices attached to that same hub to wake the PC.",
    )

    paths_found = configurable_paths()
    selected = None
    all_roots = False
    if paths_found:
        choices = [_controller_choice_label(item) for item in paths_found]
        choices += ["Enable every USB root hub (broader fallback)", "Skip controller wake"]
        choice = ui.choose(
            "Select controller",
            "Choose the controller or dongle that should wake this PC. Select the all-root fallback "
            "only when selective detection does not work.",
            choices,
        )
        if choice == len(paths_found) + 1:
            return
        if choice == len(paths_found):
            all_roots = True
        else:
            selected = paths_found[choice]
    else:
        ui.error(
            "No selectable USB controller path found",
            "No likely controller could be traced to a USB root hub. This can happen with direct "
            "Bluetooth controllers or unusual input stacks.",
        )
        if not ui.confirm(
            "Broad USB wake fallback",
            "Try enabling every USB root hub instead? This may allow keyboards, mice, and other "
            "USB devices to wake the PC too.",
            default=False,
        ):
            ui.info("Wake-on-LAN fallback", wol_fallback_summary())
            return
        all_roots = True

    settle_delay = _controller_settle_delay(ui, config)
    ui.info(
        "Administrator authorization",
        "Linux restricts changes to hardware wake permissions. The next step uses sudo once to "
        "install a udev rule and apply the wake setting immediately.\n\nNo root daemon or system-level "
        "systemd service is installed; TV automation remains a per-user service.",
    )

    try:
        result = install_wake_configuration(selected, all_roots=all_roots)
    except ControllerWakeError:
        ui.info("Wake-on-LAN fallback", wol_fallback_summary())
        raise

    if all_roots:
        save_all_roots(config, settle_delay_seconds=settle_delay)
    else:
        assert selected is not None
        save_selected_path(config, selected, settle_delay_seconds=settle_delay)
    save_config(config, paths)

    ui.info(
        "Controller wake configured",
        f"Mode: {result.mode}\n"
        f"USB root hub(s): {', '.join(result.usb_roots) or 'none'}\n"
        f"PCI controller(s): {', '.join(result.pci_controllers) or 'not required'}\n"
        f"Persistent rule: {result.rule_path}\n\n"
        "The root-hub rule remains valid when a wireless dongle re-enumerates because it does not "
        "depend on the dongle's temporary event or USB device name.",
    )

    verified = test_controller_wake(ui, config)
    config.controller_wake.verified = verified
    save_config(config, paths)
    if verified:
        ui.info("Controller wake verified", "The selected controller successfully woke this PC from suspend.")
    else:
        ui.info(
            "Controller wake not verified",
            "The wake path is configured, but the controller test did not succeed or was skipped. "
            "Sometimes controller wake simply is not possible with a particular dongle, USB "
            "controller, firmware, or BIOS/UEFI combination.\n\n" + wol_fallback_summary(),
        )


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

    try:
        run_controller_wake_setup(ui, config, paths)
    except ControllerWakeError as exc:
        ui.error(
            "Controller wake setup failed",
            f"{exc}\n\nTV automation can still be installed normally. You can rerun "
            "'atv-couch-wake controller setup' later.",
        )

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
        "No system-wide systemd service or root daemon will be created.",
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
