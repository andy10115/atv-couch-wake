"""Interactive setup wizard."""

from __future__ import annotations

import ipaddress
import socket

from .config import AppConfig, load_config, save_config
from .discovery import DeviceCandidate, discover_all, local_ipv4_networks
from .pairing import pair_device
from .paths import AppPaths
from .platform_info import inspect_platform
from .remote import TVControlError, TVController
from .systemd_integration import install_user_service, shell_command_for_logs
from .ui import UI


def _candidate_label(candidate: DeviceCandidate) -> str:
    return f"{candidate.name} — {candidate.host}:{candidate.port} ({candidate.source})"


def _subnet_for_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if not isinstance(address, ipaddress.IPv4Address):
        return ""
    for network in local_ipv4_networks():
        if address in network:
            return str(network)
    return str(ipaddress.ip_network(f"{host}/24", strict=False))


async def run_setup(
    ui: UI,
    *,
    host_override: str = "",
    install_service: bool = True,
    paths: AppPaths | None = None,
) -> AppConfig:
    paths = paths or AppPaths.from_environment()
    paths.ensure_private_directories()
    config = load_config(paths, required=False)
    config.service.ui_backend = ui.name
    config.tv.client_name = config.tv.client_name or f"atv-couch-wake ({socket.gethostname()})"

    platform = inspect_platform()
    ui.info(
        "atv-couch-wake setup",
        "This wizard will discover and pair with a Google TV / Android TV, test the selected "
        "HDMI input, and install a per-user lifecycle service.\n\n"
        f"Detected: {platform.distribution}\nKernel: {platform.kernel}\n"
        f"Atomic image: {'yes' if platform.atomic else 'no'}\n"
        f"User systemd available: {'yes' if platform.user_systemd else 'no'}",
    )

    host = host_override.strip()
    if not host:
        ui.info("TV discovery", "Make sure the television is on and connected to the same LAN.")
        candidates = await discover_all(
            mdns=config.discovery.mdns,
            subnet_scan=config.discovery.subnet_scan,
            mdns_timeout=config.discovery.mdns_timeout_seconds,
            probe_timeout=config.discovery.probe_timeout_seconds,
            configured_subnet=config.discovery.subnet,
        )
        choices = [_candidate_label(candidate) for candidate in candidates]
        choices.append("Enter an IP address manually")
        selected = ui.choose("Select television", "Choose the TV connected to this PC.", choices)
        if selected == len(candidates):
            host = ui.prompt(
                "TV address",
                "Enter the TV's IPv4 address",
                default=config.tv.host,
            )
        else:
            host = candidates[selected].host

    paired = await pair_device(host, config, ui, paths)
    config.tv.host = paired.host
    config.tv.name = paired.name
    config.tv.mac = paired.mac
    config.discovery.subnet = _subnet_for_host(paired.host)

    current_default = config.tv.hdmi_input if config.tv.hdmi_input in {1, 2, 3, 4} else 1
    # The dialog abstraction selects the first row by default, so put the current input first
    # without changing the values presented to the user.
    ordered_inputs = [current_default] + [n for n in (1, 2, 3, 4) if n != current_default]
    ordered_labels = [f"HDMI {n}" for n in ordered_inputs] + ["Do not switch inputs"]
    selected = ui.choose(
        "PC input",
        "Which HDMI input is this gaming PC connected to?",
        ordered_labels,
    )
    config.tv.hdmi_input = 0 if selected == 4 else ordered_inputs[selected]
    config.behavior.switch_input_after_wake = bool(config.tv.hdmi_input)

    config.behavior.on_startup = ui.confirm(
        "Startup behavior",
        "Turn on the TV when this user's systemd session starts?",
        default=config.behavior.on_startup,
    )
    config.behavior.on_resume = ui.confirm(
        "Resume behavior",
        "Turn on the TV and select the PC input after resume?",
        default=config.behavior.on_resume,
    )
    config.behavior.off_on_suspend = ui.confirm(
        "Suspend behavior",
        "Turn off the TV before the PC suspends?",
        default=config.behavior.off_on_suspend,
    )
    config.behavior.off_on_shutdown = ui.confirm(
        "Shutdown behavior",
        "Turn off the TV before the PC shuts down?",
        default=config.behavior.off_on_shutdown,
    )
    config.behavior.off_on_reboot = ui.confirm(
        "Reboot behavior",
        "Also turn off the TV during a reboot? Usually this should remain disabled.",
        default=config.behavior.off_on_reboot,
    )

    save_config(config, paths)

    if ui.confirm(
        "Test configuration",
        "Wake the TV and test the selected HDMI input now?",
        default=True,
    ):
        controller = TVController(config, paths)
        try:
            result = await controller.wake_and_select_input()
            if not result.success:
                ui.error("TV test failed", result.message)
            elif config.tv.hdmi_input:
                correct = ui.confirm(
                    "Input test",
                    f"Did the TV switch to HDMI {config.tv.hdmi_input}?",
                    default=True,
                )
                if not correct:
                    config.behavior.switch_input_after_wake = False
                    save_config(config, paths)
                    ui.info(
                        "Input switching disabled",
                        "Your TV may ignore discrete HDMI input keycodes. Power automation remains enabled.",
                    )
        except TVControlError as exc:
            ui.error("TV test failed", str(exc))

    if install_service:
        if not platform.user_systemd:
            ui.error(
                "Service not installed",
                "A working per-user systemd manager was not detected. Manual TV commands still work.",
            )
        elif ui.confirm(
            "Install automation",
            "Enable the per-user sleep, resume, shutdown, reboot, and startup watcher?",
            default=True,
        ):
            unit = install_user_service(paths)
            ui.info(
                "Setup complete",
                f"Installed {unit}.\n\nFollow logs with:\n{shell_command_for_logs()}",
            )
            return config

    ui.info(
        "Setup complete",
        "Configuration and pairing were saved. Run "
        "'atv-couch-wake service install' later to enable automation.",
    )
    return config
