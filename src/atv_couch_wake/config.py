"""Configuration loading and saving."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .paths import AppPaths


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class TVConfig:
    host: str = ""
    port: int = 5555
    name: str = ""
    model: str = ""
    adb_path: str = ""
    input_id: str = ""
    input_uri: str = ""
    input_label: str = ""

    @property
    def serial(self) -> str:
        if not self.host:
            return ""
        return self.host if ":" in self.host else f"{self.host}:{self.port}"


@dataclass
class BehaviorConfig:
    on_startup: bool = True
    on_resume: bool = True
    off_on_suspend: bool = True
    off_on_shutdown: bool = True
    off_on_reboot: bool = False
    switch_input_after_wake: bool = True
    startup_delay_seconds: float = 5.0
    resume_delay_seconds: float = 5.0
    wake_attempts: int = 5
    wake_retry_seconds: float = 2.0
    wake_settle_seconds: float = 2.0
    input_settle_seconds: float = 1.0
    command_timeout_seconds: float = 3.0
    connect_timeout_seconds: float = 2.5


@dataclass
class ControllerWakeConfig:
    enabled: bool = False
    controller_name: str = ""
    usb_root: str = ""
    pci_controller: str = ""
    mode: str = "selective"
    verified: bool = False
    settle_delay_seconds: float = 0.0
    rule_path: str = "/etc/udev/rules.d/90-atv-couch-wake-controller.rules"
    configured_boot_id: str = ""


@dataclass
class ServiceConfig:
    inhibitor_delay_max_seconds: float = 4.5
    ui_backend: str = "auto"


@dataclass
class AppConfig:
    tv: TVConfig = field(default_factory=TVConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    controller_wake: ControllerWakeConfig = field(default_factory=ControllerWakeConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        def accepted(model: type[Any], section: str) -> dict[str, Any]:
            allowed = {item.name for item in fields(model)}
            values = data.get(section, {})
            if not isinstance(values, dict):
                return {}
            return {key: value for key, value in values.items() if key in allowed}

        # Ignore fields from older configuration formats so in-place upgrades
        # can load before the user reruns setup.
        return cls(
            tv=TVConfig(**accepted(TVConfig, "tv")),
            behavior=BehaviorConfig(**accepted(BehaviorConfig, "behavior")),
            controller_wake=ControllerWakeConfig(**accepted(ControllerWakeConfig, "controller_wake")),
            service=ServiceConfig(**accepted(ServiceConfig, "service")),
        )

    def to_toml(self) -> str:
        t = self.tv
        b = self.behavior
        c = self.controller_wake
        s = self.service
        return f"""# atv-couch-wake configuration

[tv]
host = {_toml_string(t.host)}
port = {t.port}
name = {_toml_string(t.name)}
model = {_toml_string(t.model)}
adb_path = {_toml_string(t.adb_path)}
input_id = {_toml_string(t.input_id)}
input_uri = {_toml_string(t.input_uri)}
input_label = {_toml_string(t.input_label)}

[behavior]
on_startup = {str(b.on_startup).lower()}
on_resume = {str(b.on_resume).lower()}
off_on_suspend = {str(b.off_on_suspend).lower()}
off_on_shutdown = {str(b.off_on_shutdown).lower()}
off_on_reboot = {str(b.off_on_reboot).lower()}
switch_input_after_wake = {str(b.switch_input_after_wake).lower()}
startup_delay_seconds = {b.startup_delay_seconds}
resume_delay_seconds = {b.resume_delay_seconds}
wake_attempts = {b.wake_attempts}
wake_retry_seconds = {b.wake_retry_seconds}
wake_settle_seconds = {b.wake_settle_seconds}
input_settle_seconds = {b.input_settle_seconds}
command_timeout_seconds = {b.command_timeout_seconds}
connect_timeout_seconds = {b.connect_timeout_seconds}

[controller_wake]
enabled = {str(c.enabled).lower()}
controller_name = {_toml_string(c.controller_name)}
usb_root = {_toml_string(c.usb_root)}
pci_controller = {_toml_string(c.pci_controller)}
mode = {_toml_string(c.mode)}
verified = {str(c.verified).lower()}
settle_delay_seconds = {c.settle_delay_seconds}
rule_path = {_toml_string(c.rule_path)}
configured_boot_id = {_toml_string(c.configured_boot_id)}

[service]
inhibitor_delay_max_seconds = {s.inhibitor_delay_max_seconds}
ui_backend = {_toml_string(s.ui_backend)}
"""


def load_config(paths: AppPaths | None = None, *, required: bool = True) -> AppConfig:
    paths = paths or AppPaths.from_environment()
    if not paths.config_file.exists():
        if required:
            raise FileNotFoundError(
                f"No configuration found at {paths.config_file}. Run 'atv-couch-wake setup' first."
            )
        return AppConfig()
    with paths.config_file.open("rb") as handle:
        return AppConfig.from_dict(tomllib.load(handle))


def save_config(config: AppConfig, paths: AppPaths | None = None) -> Path:
    paths = paths or AppPaths.from_environment()
    paths.ensure_private_directories()
    temporary = paths.config_file.with_suffix(".tmp")
    temporary.write_text(config.to_toml(), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(paths.config_file)
    return paths.config_file
