"""Optional controller-to-PC wake configuration."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .diagnostics import ControllerWakePath, controller_wake_paths, parse_input_devices

RULE_PATH = Path("/etc/udev/rules.d/90-atv-couch-wake-controller.rules")
USB_ROOT_RE = re.compile(r"usb\d+")
PCI_RE = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")


class ControllerWakeError(RuntimeError):
    """Controller wake configuration could not be completed."""


@dataclass(frozen=True)
class WakeConfigurationResult:
    rule_path: str
    usb_roots: tuple[str, ...]
    pci_controllers: tuple[str, ...]
    mode: str


def detect_controller_wake_paths() -> list[ControllerWakePath]:
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8")
    except OSError:
        return []
    return controller_wake_paths(parse_input_devices(text))


def configurable_paths(paths: list[ControllerWakePath] | None = None) -> list[ControllerWakePath]:
    paths = detect_controller_wake_paths() if paths is None else paths
    return [item for item in paths if USB_ROOT_RE.fullmatch(item.usb_root)]


def render_selective_rule(path: ControllerWakePath) -> str:
    if not USB_ROOT_RE.fullmatch(path.usb_root):
        raise ControllerWakeError(f"Invalid USB root name: {path.usb_root}")
    lines = [
        "# Managed by atv-couch-wake. Re-run controller setup to replace this file.",
        "# The stable USB root is armed rather than the leaf controller device so",
        "# wireless dongles may re-enumerate without losing the wake configuration.",
    ]
    if PCI_RE.fullmatch(path.pci_controller):
        lines.append(
            f'SUBSYSTEM=="usb", KERNEL=="{path.usb_root}", '
            f'KERNELS=="{path.pci_controller}", TEST=="power/wakeup", ATTR{{power/wakeup}}="enabled"'
        )
        lines.append(
            f'SUBSYSTEM=="pci", KERNEL=="{path.pci_controller}", '
            'TEST=="power/wakeup", ATTR{power/wakeup}="enabled"'
        )
    else:
        lines.append(
            f'SUBSYSTEM=="usb", KERNEL=="{path.usb_root}", '
            'TEST=="power/wakeup", ATTR{power/wakeup}="enabled"'
        )
    return "\n".join(lines) + "\n"


def render_all_roots_rule() -> str:
    return """# Managed by atv-couch-wake. Fallback mode: arm every USB root hub.
# Any wake-capable device on an armed root hub may wake the PC.
SUBSYSTEM=="usb", KERNEL=="usb*", TEST=="power/wakeup", ATTR{power/wakeup}="enabled"
"""


def authorize_sudo() -> None:
    """Request one visible administrator authorization before captured sudo calls."""
    try:
        result = subprocess.run(["sudo", "-v"], check=False, timeout=120)
    except FileNotFoundError as exc:
        raise ControllerWakeError("sudo is required to change kernel wake permissions.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ControllerWakeError("Administrator authorization timed out.") from exc
    if result.returncode != 0:
        raise ControllerWakeError("Administrator authorization was not granted.")


def _run_sudo(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["sudo", *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise ControllerWakeError("sudo is required to change kernel wake permissions.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ControllerWakeError("Administrator authorization timed out.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise ControllerWakeError(f"Privileged wake configuration failed: {detail}")
    return result


def _write_sysfs_enabled(path: Path) -> None:
    if not path.exists():
        return
    _run_sudo(["tee", str(path)], input_text="enabled\n")


def install_wake_configuration(
    path: ControllerWakePath | None, *, all_roots: bool = False
) -> WakeConfigurationResult:
    authorize_sudo()
    if all_roots:
        rule = render_all_roots_rule()
        roots = tuple(sorted(item.name for item in Path("/sys/bus/usb/devices").glob("usb*")))
        pcis: tuple[str, ...] = ()
        mode = "all-roots"
    else:
        if path is None:
            raise ControllerWakeError("A controller wake path is required for selective mode.")
        rule = render_selective_rule(path)
        roots = (path.usb_root,)
        pcis = (path.pci_controller,) if PCI_RE.fullmatch(path.pci_controller) else ()
        mode = "selective"

    _run_sudo(["mkdir", "-p", str(RULE_PATH.parent)])
    _run_sudo(["tee", str(RULE_PATH)], input_text=rule)
    _run_sudo(["chmod", "0644", str(RULE_PATH)])

    for root in roots:
        if USB_ROOT_RE.fullmatch(root):
            _write_sysfs_enabled(Path("/sys/bus/usb/devices") / root / "power/wakeup")
    for pci in pcis:
        if PCI_RE.fullmatch(pci):
            _write_sysfs_enabled(Path("/sys/bus/pci/devices") / pci / "power/wakeup")

    # The immediate sysfs writes make the current boot testable. Reloading udev
    # makes the rule persistent for future boots/re-enumeration.
    _run_sudo(["udevadm", "control", "--reload-rules"])
    return WakeConfigurationResult(str(RULE_PATH), roots, pcis, mode)


def remove_wake_configuration() -> None:
    authorize_sudo()
    _run_sudo(["rm", "-f", str(RULE_PATH)])
    _run_sudo(["udevadm", "control", "--reload-rules"])


def save_selected_path(config: AppConfig, path: ControllerWakePath, *, settle_delay_seconds: float) -> None:
    config.controller_wake.enabled = True
    config.controller_wake.controller_name = path.name
    config.controller_wake.usb_root = path.usb_root
    config.controller_wake.pci_controller = (
        path.pci_controller if PCI_RE.fullmatch(path.pci_controller) else ""
    )
    config.controller_wake.mode = "selective"
    config.controller_wake.verified = False
    config.controller_wake.settle_delay_seconds = max(0.0, settle_delay_seconds)
    config.controller_wake.rule_path = str(RULE_PATH)


def save_all_roots(config: AppConfig, *, settle_delay_seconds: float) -> None:
    config.controller_wake.enabled = True
    config.controller_wake.controller_name = "All USB root hubs"
    config.controller_wake.usb_root = "*"
    config.controller_wake.pci_controller = ""
    config.controller_wake.mode = "all-roots"
    config.controller_wake.verified = False
    config.controller_wake.settle_delay_seconds = max(0.0, settle_delay_seconds)
    config.controller_wake.rule_path = str(RULE_PATH)


def wol_fallback_summary() -> str:
    return (
        "Controller wake is hardware-dependent and sometimes it simply will not work.\n\n"
        "A controller or wireless dongle must actually generate a wake event, the USB root hub "
        "and parent controller must support wake, and BIOS/UEFI must allow the machine to resume "
        "from that hardware. Some dongle revisions cannot do it at all.\n\n"
        "Wake-on-LAN is the best fallback for a couch PC:\n\n"
        "1. Prefer wired Ethernet and enable Wake-on-LAN / PCIe wake in BIOS or UEFI.\n"
        "2. In Linux, check the Ethernet interface with `ethtool <interface>` and look for "
        "`Supports Wake-on: g` and `Wake-on: g`.\n"
        "3. If supported but disabled, enable magic-packet wake for the connection. NetworkManager "
        "users can use:\n"
        '   `nmcli connection modify "<connection name>" '
        "802-3-ethernet.wake-on-lan magic`\n"
        "   then reconnect or reboot.\n"
        "4. Install any reputable Wake-on-LAN app on your phone and add the PC's Ethernet MAC "
        "address. Keep the phone on the same LAN for the simplest setup.\n"
        "5. Send the magic packet from the phone. Once the PC wakes, atv-couch-wake still handles "
        "the TV wake and input switch, so you keep the same console-like effect—only the initial "
        "wake comes from the phone instead of the controller.\n\n"
        "Do not expose Wake-on-LAN or ADB directly to the public internet. For remote access, use "
        "a VPN into your home network."
    )
