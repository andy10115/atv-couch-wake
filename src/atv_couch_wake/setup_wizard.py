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
    controller_wake_reboot_required,
    install_wake_configuration,
    save_all_roots,
    save_selected_path,
    wol_fallback_summary,
)
from .diagnostics import collect_diagnostics, render_controller_wake
from .paths import AppPaths
from .platform_info import inspect_platform
from .systemd_integration import install_user_service, shell_command_for_logs
from .ui import UI, UserCancelled

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
        "Does the TV require a pairing code and show a separate pairing address/port? (usually no)",
        default=False,
    ):
        return
    address = ui.prompt(
        "Pairing address",
        "Enter the pairing address shown by the TV, including its port (for example 10.0.0.42:37123)",
    )
    code = ui.prompt("Pairing code", "Enter the six-digit pairing code shown by the TV")
    try:
        await controller.pair(address, code)
    except TVControlError as exc:
        ui.error(
            "Wireless pairing failed",
            f"{exc}\n\nSetup will still try the normal ADB connection. You can rerun pairing later if "
            "your TV requires it.",
        )
        return
    ui.info("Wireless pairing complete", "The PC paired successfully. Continuing with the ADB connection.")


async def _test_power(ui: UI, controller: ADBController) -> tuple[bool | None, bool | None]:
    if not ui.confirm(
        "Test power control",
        "Test ADB sleep and wake now? Keep the physical remote nearby during this first test.",
        default=True,
    ):
        return None, None

    ui.info("Power-off test", "Sending KEYCODE_SLEEP. The TV panel should turn off.")
    try:
        off = await controller.set_power(False)
    except TVControlError as exc:
        ui.error(
            "Power-off test failed",
            f"{exc}\n\nSetup will continue. You can retest later with 'atv-couch-wake test power-off'.",
        )
        return False, None
    if not off.success:
        ui.error(
            "Power-off test failed",
            f"{off.message}\n\nSetup will continue. You can retest later with "
            "'atv-couch-wake test power-off'.",
        )
        return False, None
    off_confirmed = ui.confirm("Power-off result", "Did the TV turn off?", default=True)
    if not off_confirmed:
        ui.info(
            "Power-off not verified",
            "The command completed, but you reported that the TV did not turn off. Setup will continue "
            "so input selection and automation can still be configured.",
        )
        return False, None

    ui.info("Power-on test", "Waiting five seconds, then sending KEYCODE_WAKEUP.")
    await asyncio.sleep(5.0)
    try:
        on = await controller.set_power(True)
    except TVControlError as exc:
        ui.error(
            "Power-on test failed",
            f"{exc}\n\nUse the physical remote if needed. Setup will continue, and you can retest later "
            "with 'atv-couch-wake test power-on'.",
        )
        return True, False
    if not on.success:
        ui.error(
            "Power-on test failed",
            f"{on.message}\n\nUse the physical remote if needed. Setup will continue, and you can "
            "retest later with 'atv-couch-wake test power-on'.",
        )
        return True, False
    on_confirmed = ui.confirm("Power-on result", "Did the TV turn back on?", default=True)
    if not on_confirmed:
        ui.info(
            "Power-on not verified",
            "The command completed, but you reported that the TV did not wake. Setup will continue.",
        )
    return True, on_confirmed


async def _choose_input(ui: UI, controller: ADBController, config: AppConfig) -> bool:
    existing_input = bool(config.tv.input_uri)
    try:
        inputs = await controller.discover_inputs()
    except TVControlError as exc:
        ui.error(
            "Input discovery failed",
            f"{exc}\n\nPower automation can still be configured. You can rerun "
            "'atv-couch-wake test inputs' later.",
        )
        if existing_input:
            ui.info("Keeping existing input", "The previously saved input will be kept unchanged.")
        return existing_input
    if not inputs:
        ui.error(
            "No passthrough inputs found",
            "The TV did not expose any physical passthrough inputs through Android's TV Input "
            "Framework. Power automation can still be configured.",
        )
        if existing_input:
            ui.info("Keeping existing input", "The previously saved input will be kept unchanged.")
        return existing_input

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
        try:
            await controller.select_input(candidate.uri)
        except TVControlError as exc:
            ui.error(
                "Input test failed",
                f"Could not launch {candidate.hardware_id}: {exc}\n\nContinuing to the next input.",
            )
            continue
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
        if existing_input:
            ui.info(
                "Keeping existing input",
                "The previously saved input "
                f"({config.tv.input_label or config.tv.input_id}) will remain configured.",
            )
        return existing_input

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
    return True


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
    while True:
        raw = ui.prompt(
            "Settle delay",
            "Seconds to wait before the PC enters suspend. The user-service watcher is limited by "
            "logind's delay-inhibitor window, so long values may be capped automatically",
            default=default,
        )
        try:
            value = float(raw)
        except ValueError:
            ui.error("Invalid settle delay", "Enter a number of seconds, for example 2 or 2.5.")
            continue
        return max(0.0, value)


def test_controller_wake(ui: UI, config: AppConfig) -> bool:
    if controller_wake_reboot_required(config):
        raise ControllerWakeError(
            "Controller wake was configured during the current boot. Reboot before running a suspend "
            "test so the persistent wake rule and hardware topology start from a clean boot state."
        )
    ui.info(
        "Controller wake test",
        "This manual test should only be run after rebooting since controller wake was configured.\n\n"
        "1. Make sure another wake method is available.\n"
        "2. Turn the selected controller off.\n"
        "3. The PC will suspend.\n"
        "4. Wait until it is fully asleep, then turn the controller back on.",
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


def run_controller_wake_setup(ui: UI, config: AppConfig, paths: AppPaths) -> bool:
    if not ui.confirm(
        "Controller wake",
        "Would you like to try allowing a USB controller or wireless controller dongle to wake "
        "this PC from suspend?\n\nThis is optional and hardware-dependent.",
        default=True,
    ):
        return False

    ui.info(
        "How controller wake works",
        "atv-couch-wake traces the selected controller to its USB root hub and, when available, "
        "its parent PCI USB controller. It enables wake on that stable hardware path rather than "
        "the temporary controller device itself.\n\nThis matters for wireless dongles that re-enumerate or "
        "change device identity when the controller turns on or off.\n\nEnabling a USB root hub may also "
        "allow other wake-capable devices attached to that same hub to wake the PC.\n\n"
        "Controller wake cannot be guaranteed. Some controllers, dongles, USB controllers, ports, "
        "and firmware combinations simply cannot generate a usable wake event.",
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
            return False
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
            return False
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
    except ControllerWakeError as exc:
        ui.error(
            "Controller wake setup failed",
            f"{exc}\n\nTV automation is unaffected and setup will continue. You can retry later with "
            "'atv-couch-wake controller setup'.",
        )
        ui.info("Wake-on-LAN fallback", wol_fallback_summary())
        return False

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
        "depend on the dongle's temporary event or USB device name.\n\n"
        "A reboot is required before controller wake should be tested. Do not run a suspend test "
        "during this setup session.",
    )
    return True


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
        ui.info(
            "Continuing with a warning",
            "Setup will still try the ADB connection. If debugging is not actually enabled, "
            "authorization will fail. You can exit now and rerun setup later if needed.",
        )
        if not ui.confirm("Continue setup", "Try connecting to the TV anyway?", default=False):
            raise UserCancelled("Setup paused until TV debugging is enabled.")

    ui.info("Configure standby networking", POWER_GUIDE)
    if not ui.confirm(
        "Power settings",
        "Have you selected Optimized energy mode and/or enabled Quick Start / Quick Resume?",
        default=True,
    ):
        ui.info(
            "Continuing with a warning",
            "Not every TV exposes the same power options, so setup will continue. If wake later fails "
            "while the panel is off, revisit the TV's standby networking or quick-start settings.",
        )

    default_address = config.tv.serial or config.tv.host
    if host_override:
        _set_address(config, _validate_host(host_override))
    else:
        while True:
            address = ui.prompt(
                "TV address",
                "Enter the TV's IP address. Include a port only when it is not 5555",
                default=default_address,
            )
            try:
                _set_address(config, _validate_host(address))
                break
            except TVControlError as exc:
                ui.error("Invalid TV address", str(exc))
    controller = ADBController(config)

    await _optional_wireless_pair(ui, controller)
    while True:
        try:
            await _authorize(ui, controller)
            break
        except (ADBUnauthorized, TVControlError) as exc:
            ui.error(
                "TV authorization not complete",
                f"{exc}\n\nCheck the TV's debugging setting, IP address, and authorization prompt.",
            )
            if not ui.confirm(
                "Retry ADB authorization", "Try connecting and authorizing again?", default=True
            ):
                raise UserCancelled("Setup paused before ADB authorization completed.") from exc

    try:
        config.tv.model = await controller.model()
    except TVControlError as exc:
        config.tv.model = ""
        ui.error(
            "TV model lookup failed",
            f"{exc}\n\nThe ADB connection is authorized, so setup will continue without a model name.",
        )
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

    power_off_verified, power_on_verified = await _test_power(ui, controller)
    if power_off_verified is True and power_on_verified is False:
        ui.info(
            "Restore the TV before input testing",
            "The TV turned off but did not wake during the test. Turn it back on with the physical "
            "remote before continuing to input discovery.",
        )
        if ui.confirm("TV restored", "Is the TV back on and ready for input testing?", default=True):
            input_configured = await _choose_input(ui, controller, config)
        else:
            input_configured = bool(config.tv.input_uri)
            ui.info(
                "Input test skipped",
                "Setup will continue. Any previously saved input remains unchanged.",
            )
    else:
        input_configured = await _choose_input(ui, controller, config)

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

    run_controller_wake_setup(ui, config, paths)
    controller_configured = config.controller_wake.enabled
    controller_reboot_required = controller_wake_reboot_required(config)

    service_installed = False
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
        try:
            unit = install_user_service(paths)
            service_installed = True
            ui.info(
                "Automation installed",
                f"Installed user service:\n{unit}\n\nFollow its logs with:\n{shell_command_for_logs()}",
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            ui.error(
                "Automation installation failed",
                f"{exc}\n\nYour TV configuration is saved. Fix the user-service issue and run "
                "'atv-couch-wake service install' later.",
            )

    power_summary = (
        "verified"
        if power_off_verified is True and power_on_verified is True
        else "partially verified"
        if power_off_verified is True or power_on_verified is True
        else "not verified"
        if power_off_verified is False or power_on_verified is False
        else "skipped"
    )
    controller_summary = (
        "configured; reboot required"
        if controller_reboot_required
        else "configured"
        if controller_configured
        else "skipped/not configured"
    )
    summary = (
        f"TV ADB connection: verified\n"
        f"TV power control: {power_summary}\n"
        f"Input switching: {'configured' if input_configured else 'skipped/not verified'}\n"
        f"Controller wake: {controller_summary}\n"
        f"Lifecycle watcher: {'installed and running' if service_installed else 'not installed'}\n\n"
        "Startup and resume TV commands wait five seconds before the first ADB attempt so the user "
        "session, network, and TV standby services have time to settle."
    )
    ui.info("Setup summary", summary)

    if controller_reboot_required:
        ui.info(
            "Reboot required before controller testing",
            "The controller wake rule has been installed, but do not test it during this setup session.\n\n"
            "1. Reboot the PC first.\n"
            "2. After the reboot, suspend the PC normally from your desktop or gaming interface.\n"
            "3. Wait until the PC is fully asleep.\n"
            "4. Turn the controller back on.\n\n"
            "If the controller cannot wake the PC, that may be a hardware or firmware limitation rather "
            "than a setup failure. Try a different physical USB port, or use Wake-on-LAN as a manually "
            "configured fallback. TV wake and input switching will still work whenever the PC starts "
            "or resumes.",
        )
        if service_installed and ui.confirm(
            "Reboot now",
            "Reboot now so the controller wake rule and lifecycle watcher start from a clean boot?",
            default=True,
        ):
            result = subprocess.run(["systemctl", "reboot"], check=False)
            if result.returncode != 0:
                ui.error(
                    "Automatic reboot failed",
                    "Run 'systemctl reboot' manually before testing controller wake.",
                )
            return config

    if not service_installed:
        ui.info(
            "Setup complete",
            "Configuration was saved. Run 'atv-couch-wake service install' later to enable automatic "
            "startup, resume, suspend, and shutdown handling.",
        )
    else:
        ui.info("Setup complete", "TV lifecycle automation is installed and ready.")
    return config
