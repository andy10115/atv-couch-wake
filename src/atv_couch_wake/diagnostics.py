"""Read-only diagnostics for TV, systemd, and controller wake topology."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adb_control import ADBController, TVControlError, find_adb
from .config import load_config
from .paths import AppPaths
from .platform_info import executable_status, inspect_platform
from .systemd_integration import service_status


@dataclass(frozen=True)
class InputDevice:
    name: str
    phys: str
    handlers: str


@dataclass(frozen=True)
class ControllerWakePath:
    name: str
    phys: str
    event: str
    usb_device: str
    usb_root: str
    usb_root_wakeup: str
    pci_controller: str
    pci_wakeup: str
    root_armed: bool


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


def _read_wakeup(device_path: Path | None) -> str:
    if device_path is None:
        return "not-found"
    wake_file = device_path / "power/wakeup"
    try:
        return wake_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unavailable"


def _first_matching_ancestor(path: Path, pattern: str) -> Path | None:
    regex = re.compile(pattern, re.IGNORECASE)
    for candidate in (path, *path.parents):
        if regex.fullmatch(candidate.name):
            return candidate
    return None


def _event_handler(device: InputDevice) -> str:
    return next((part for part in device.handlers.split() if re.fullmatch(r"event\d+", part)), "")


def _root_from_phys(phys: str, usb_base: Path) -> tuple[Path | None, Path | None]:
    match = re.search(r"usb-(?P<pci>[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7])-", phys, re.I)
    if not match or not usb_base.exists():
        return None, None
    pci_name = match.group("pci").lower()
    for root_link in sorted(usb_base.glob("usb*")):
        try:
            resolved = root_link.resolve()
        except OSError:
            continue
        if any(parent.name.lower() == pci_name for parent in (resolved, *resolved.parents)):
            pci = _first_matching_ancestor(resolved, r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
            return root_link, pci
    return None, None


def controller_wake_paths(
    devices: list[InputDevice],
    *,
    input_base: Path = Path("/sys/class/input"),
    usb_base: Path = Path("/sys/bus/usb/devices"),
    pci_base: Path = Path("/sys/bus/pci/devices"),
) -> list[ControllerWakePath]:
    """Resolve likely controllers to their USB root hub and PCI parent wake state."""
    results: list[ControllerWakePath] = []
    for device in likely_controllers(devices):
        event = _event_handler(device)
        resolved: Path | None = None
        if event:
            try:
                resolved = (input_base / event / "device").resolve(strict=True)
            except OSError:
                resolved = None

        usb_device_path = _first_matching_ancestor(resolved, r"\d+-\d+(?:\.\d+)*") if resolved else None
        usb_root_path = _first_matching_ancestor(resolved, r"usb\d+") if resolved else None
        pci_path = (
            _first_matching_ancestor(resolved, r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
            if resolved
            else None
        )

        if usb_root_path is None:
            fallback_root, fallback_pci = _root_from_phys(device.phys, usb_base)
            usb_root_path = fallback_root
            pci_path = pci_path or fallback_pci

        usb_root_name = usb_root_path.name if usb_root_path else "not-found"
        pci_name = pci_path.name if pci_path else "not-found"
        usb_root_lookup = usb_base / usb_root_name if usb_root_name != "not-found" else None
        pci_lookup = pci_base / pci_name if pci_name != "not-found" else None
        root_state = _read_wakeup(usb_root_lookup or usb_root_path)
        pci_state = _read_wakeup(pci_lookup or pci_path)

        results.append(
            ControllerWakePath(
                name=device.name,
                phys=device.phys,
                event=event or "not-found",
                usb_device=usb_device_path.name if usb_device_path else "not-found",
                usb_root=usb_root_name,
                usb_root_wakeup=root_state,
                pci_controller=pci_name,
                pci_wakeup=pci_state,
                root_armed=root_state == "enabled",
            )
        )
    return results


async def collect_diagnostics(paths: AppPaths | None = None) -> dict[str, Any]:
    paths = paths or AppPaths.from_environment()
    platform = inspect_platform()
    service = service_status(paths)
    report: dict[str, Any] = {
        "platform": asdict(platform),
        "executables": executable_status(),
        "paths": {
            "config": str(paths.config_file),
            "user_unit": str(paths.user_unit_file),
        },
        "files": {"config_exists": paths.config_file.exists()},
        "service": asdict(service),
    }

    try:
        report["adb_path"] = find_adb()
        report["adb_available"] = True
    except TVControlError as exc:
        report["adb_available"] = False
        report["adb_error"] = str(exc)

    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8")
    except OSError:
        text = ""
    devices = parse_input_devices(text)
    controllers = likely_controllers(devices)
    report["controllers"] = [asdict(item) for item in controllers]
    report["controller_wake_paths"] = [asdict(item) for item in controller_wake_paths(devices)]
    report["usb_wakeup"] = _wakeup_entries(Path("/sys/bus/usb/devices"))
    report["pci_wakeup"] = _wakeup_entries(Path("/sys/bus/pci/devices"))

    try:
        config = load_config(paths)
        report["configured_tv"] = {
            "host": config.tv.host,
            "port": config.tv.port,
            "serial": config.tv.serial,
            "name": config.tv.name,
            "model": config.tv.model,
            "adb_path": config.tv.adb_path,
            "input_id": config.tv.input_id,
            "input_label": config.tv.input_label,
            "input_uri": config.tv.input_uri,
        }
        report["configured_controller_wake"] = asdict(config.controller_wake)
        try:
            status = await ADBController(config).status()
            report["tv_status"] = {
                "reachable": True,
                "serial": status.serial,
                "authorized": status.authorized,
                "is_on": status.is_on,
                "model": status.model,
                "current_input_id": status.current_input_id,
            }
        except TVControlError as exc:
            report["tv_status"] = {"reachable": False, "error": str(exc)}
    except FileNotFoundError as exc:
        report["configuration_error"] = str(exc)
    return report


def render_controller_wake(report: dict[str, Any]) -> str:
    lines = ["Controller USB wake paths", "=========================", ""]
    paths = report.get("controller_wake_paths", [])
    if not paths:
        lines.append("No likely controller input devices were detected.")
        return "\n".join(lines)
    for item in paths:
        status = "READY" if item["root_armed"] else "NOT ARMED"
        lines += [
            f"{item['name']}",
            f"  input event: {item['event']}",
            f"  physical path: {item['phys'] or 'unknown'}",
            f"  USB device: {item['usb_device']}",
            f"  USB root hub: {item['usb_root']}",
            f"  USB root wake: {item['usb_root_wakeup']} [{status}]",
            f"  parent PCI controller: {item['pci_controller']}",
            f"  parent PCI wake: {item['pci_wakeup']}",
            "",
        ]
    lines.append("This check is read-only; it does not enable or disable wake sources.")
    return "\n".join(lines)


def render_diagnostics(report: dict[str, Any]) -> str:
    platform = report["platform"]
    lines = [
        "atv-couch-wake diagnostics",
        "==========================",
        f"OS: {platform['distribution']}",
        f"Kernel: {platform['kernel']}",
        f"Atomic: {platform['atomic']}",
        f"Python: {platform['python_version']}",
        f"ADB available: {report.get('adb_available', False)}",
    ]
    if report.get("adb_path"):
        lines.append(f"ADB path: {report['adb_path']}")
    if report.get("adb_error"):
        lines.append(f"ADB error: {report['adb_error']}")

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
            f"  name: {tv['name'] or 'unnamed'}",
            f"  model: {tv['model'] or 'unknown'}",
            f"  serial: {tv['serial']}",
            f"  input: {tv['input_label'] or 'disabled'}",
            f"  input id: {tv['input_id'] or 'none'}",
        ]
    if "tv_status" in report:
        lines += ["", "TV connection:"]
        for key, value in report["tv_status"].items():
            lines.append(f"  {key}: {value}")

    if "configured_controller_wake" in report:
        wake = report["configured_controller_wake"]
        lines += [
            "",
            "Saved controller wake configuration:",
            f"  enabled: {wake['enabled']}",
            f"  controller: {wake['controller_name'] or 'none'}",
            f"  mode: {wake['mode']}",
            f"  USB root: {wake['usb_root'] or 'none'}",
            f"  PCI controller: {wake['pci_controller'] or 'none'}",
            f"  settle delay: {wake['settle_delay_seconds']} seconds",
            f"  verified: {wake['verified']}",
        ]

    lines += ["", render_controller_wake(report)]
    enabled_usb = [item for item in report.get("usb_wakeup", []) if item["state"] == "enabled"]
    enabled_pci = [item for item in report.get("pci_wakeup", []) if item["state"] == "enabled"]
    lines += [
        "",
        f"All USB wake-enabled devices: {', '.join(item['device'] for item in enabled_usb) or 'none'}",
        f"All PCI wake-enabled devices: {', '.join(item['device'] for item in enabled_pci) or 'none'}",
        "",
        "This report is read-only; it does not change USB or PCI wake settings.",
    ]
    return "\n".join(lines)


def diagnostics_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=str)
