"""Read-only diagnostics for TV, systemd, and controller wake topology."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import load_config
from .paths import AppPaths
from .platform_info import executable_status, inspect_platform
from .remote import TVControlError, TVController
from .systemd_integration import service_status


@dataclass(frozen=True)
class InputDevice:
    name: str
    phys: str
    handlers: str


def parse_input_devices(text: str) -> list[InputDevice]:
    devices: list[InputDevice] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        name_match = re.search(r'^N: Name="(.*)"$', block, re.MULTILINE)
        if not name_match:
            continue
        phys_match = re.search(r"^P: Phys=(.*)$", block, re.MULTILINE)
        handlers_match = re.search(r"^H: Handlers=(.*)$", block, re.MULTILINE)
        devices.append(
            InputDevice(
                name=name_match.group(1),
                phys=phys_match.group(1) if phys_match else "",
                handlers=handlers_match.group(1) if handlers_match else "",
            )
        )
    return devices


def likely_controllers(devices: list[InputDevice]) -> list[InputDevice]:
    patterns = ("gamepad", "joystick", "gamesir", "8bitdo", "dualsense", "xbox", "controller")
    return [item for item in devices if any(pattern in item.name.casefold() for pattern in patterns)]


def _wakeup_entries(base: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if not base.exists():
        return entries
    for wake_file in sorted(base.glob("*/power/wakeup")):
        try:
            value = wake_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        entries.append({"device": wake_file.parent.parent.name, "state": value})
    return entries


async def collect_diagnostics(paths: AppPaths | None = None) -> dict[str, Any]:
    paths = paths or AppPaths.from_environment()
    platform = inspect_platform()
    service = service_status(paths)
    report: dict[str, Any] = {
        "platform": asdict(platform),
        "executables": executable_status(),
        "paths": {
            "config": str(paths.config_file),
            "certificate": str(paths.cert_file),
            "key": str(paths.key_file),
            "user_unit": str(paths.user_unit_file),
        },
        "files": {
            "config_exists": paths.config_file.exists(),
            "certificate_exists": paths.cert_file.exists(),
            "key_exists": paths.key_file.exists(),
        },
        "service": asdict(service),
    }

    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8")
    except OSError:
        text = ""
    controllers = likely_controllers(parse_input_devices(text))
    report["controllers"] = [asdict(item) for item in controllers]
    report["usb_wakeup"] = _wakeup_entries(Path("/sys/bus/usb/devices"))
    report["pci_wakeup"] = _wakeup_entries(Path("/sys/bus/pci/devices"))

    try:
        config = load_config(paths)
        report["configured_tv"] = {
            "host": config.tv.host,
            "name": config.tv.name,
            "mac": config.tv.mac,
            "hdmi_input": config.tv.hdmi_input,
            "subnet": config.discovery.subnet,
        }
        try:
            status = await TVController(config, paths).status()
            report["tv_status"] = {
                "reachable": True,
                "host": status.host,
                "is_on": status.is_on,
                "current_app": status.current_app,
                "device_info": str(status.device_info),
            }
        except TVControlError as exc:
            report["tv_status"] = {"reachable": False, "error": str(exc)}
    except FileNotFoundError as exc:
        report["configuration_error"] = str(exc)
    return report


def render_diagnostics(report: dict[str, Any]) -> str:
    lines: list[str] = []
    platform = report["platform"]
    lines += [
        "atv-couch-wake diagnostics",
        "==============================",
        f"OS: {platform['distribution']}",
        f"Kernel: {platform['kernel']}",
        f"Atomic: {platform['atomic']}",
        f"Python: {platform['python_version']}",
        "",
        "Files:",
    ]
    for key, value in report["files"].items():
        lines.append(f"  {key}: {value}")
    service = report["service"]
    lines += [
        "",
        "User service:",
        f"  installed: {service['installed']}",
        f"  enabled: {service['enabled']}",
        f"  active: {service['active']}",
        f"  detail: {service['detail']}",
    ]
    if "configured_tv" in report:
        tv = report["configured_tv"]
        lines += [
            "",
            "Configured TV:",
            f"  name: {tv['name']}",
            f"  host: {tv['host']}",
            f"  MAC: {tv['mac']}",
            f"  HDMI input: {tv['hdmi_input'] or 'disabled'}",
        ]
    if "tv_status" in report:
        status = report["tv_status"]
        lines += ["", "TV connection:"]
        for key, value in status.items():
            lines.append(f"  {key}: {value}")

    lines += ["", "Likely controller devices:"]
    controllers = report.get("controllers", [])
    if controllers:
        for item in controllers:
            lines.append(f"  - {item['name']} | phys={item['phys']} | handlers={item['handlers']}")
    else:
        lines.append("  none detected")

    enabled_usb = [item for item in report.get("usb_wakeup", []) if item["state"] == "enabled"]
    enabled_pci = [item for item in report.get("pci_wakeup", []) if item["state"] == "enabled"]
    lines += [
        "",
        f"USB wake-enabled devices: {', '.join(item['device'] for item in enabled_usb) or 'none'}",
        f"PCI wake-enabled devices: {', '.join(item['device'] for item in enabled_pci) or 'none'}",
        "",
        "This report is read-only; it does not change USB or PCI wake settings.",
    ]
    return "\n".join(lines)


def diagnostics_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=str)
