"""Linux environment inspection."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlatformInfo:
    distribution: str
    version: str
    variant: str
    kernel: str
    atomic: bool
    systemd: bool
    user_systemd: bool
    python_version: str


def _os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def inspect_platform() -> PlatformInfo:
    release = _os_release()
    atomic = Path("/run/ostree-booted").exists() or any(
        marker in " ".join(release.values()).casefold()
        for marker in ("bazzite", "silverblue", "kinoite", "atomic")
    )
    systemd = Path("/run/systemd/system").exists() and bool(shutil.which("systemctl"))
    user_systemd = False
    if systemd:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show-environment"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            user_systemd = result.returncode == 0
        except subprocess.SubprocessError:
            pass
    return PlatformInfo(
        distribution=release.get("PRETTY_NAME", release.get("NAME", "Unknown Linux")),
        version=release.get("VERSION_ID", ""),
        variant=release.get("VARIANT_ID", ""),
        kernel=platform.release(),
        atomic=atomic,
        systemd=systemd,
        user_systemd=user_systemd,
        python_version=platform.python_version(),
    )


def executable_status() -> dict[str, bool]:
    return {
        command: bool(shutil.which(command))
        for command in ("systemctl", "loginctl", "ip", "kdialog", "zenity")
    }


def user_identity() -> tuple[int, str]:
    return os.getuid(), os.environ.get("USER", str(os.getuid()))
