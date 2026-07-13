"""Configuration loading and saving."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    name: str = ""
    mac: str = ""
    api_port: int = 6466
    pair_port: int = 6467
    hdmi_input: int = 0
    client_name: str = "atv-couch-wake"


@dataclass
class BehaviorConfig:
    on_startup: bool = True
    on_resume: bool = True
    off_on_suspend: bool = True
    off_on_shutdown: bool = True
    off_on_reboot: bool = False
    switch_input_after_wake: bool = True
    startup_delay_seconds: float = 3.0
    wake_attempts: int = 4
    wake_retry_seconds: float = 2.0
    wake_settle_seconds: float = 2.5
    command_ready_delay_seconds: float = 1.0
    command_settle_seconds: float = 3.0
    state_timeout_seconds: float = 6.0
    connect_timeout_seconds: float = 8.0
    power_attempts: int = 6


@dataclass
class DiscoveryConfig:
    mdns: bool = True
    subnet_scan: bool = True
    mdns_timeout_seconds: float = 3.0
    probe_timeout_seconds: float = 0.25
    subnet: str = ""


@dataclass
class ServiceConfig:
    inhibitor_delay_max_seconds: float = 4.5
    ui_backend: str = "auto"


@dataclass
class AppConfig:
    tv: TVConfig = field(default_factory=TVConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        return cls(
            tv=TVConfig(**data.get("tv", {})),
            behavior=BehaviorConfig(**data.get("behavior", {})),
            discovery=DiscoveryConfig(**data.get("discovery", {})),
            service=ServiceConfig(**data.get("service", {})),
        )

    def to_toml(self) -> str:
        t = self.tv
        b = self.behavior
        d = self.discovery
        s = self.service
        return f"""# atv-couch-wake configuration

[tv]
host = {_toml_string(t.host)}
name = {_toml_string(t.name)}
mac = {_toml_string(t.mac)}
api_port = {t.api_port}
pair_port = {t.pair_port}
hdmi_input = {t.hdmi_input}
client_name = {_toml_string(t.client_name)}

[behavior]
on_startup = {str(b.on_startup).lower()}
on_resume = {str(b.on_resume).lower()}
off_on_suspend = {str(b.off_on_suspend).lower()}
off_on_shutdown = {str(b.off_on_shutdown).lower()}
off_on_reboot = {str(b.off_on_reboot).lower()}
switch_input_after_wake = {str(b.switch_input_after_wake).lower()}
startup_delay_seconds = {b.startup_delay_seconds}
wake_attempts = {b.wake_attempts}
wake_retry_seconds = {b.wake_retry_seconds}
wake_settle_seconds = {b.wake_settle_seconds}
command_ready_delay_seconds = {b.command_ready_delay_seconds}
command_settle_seconds = {b.command_settle_seconds}
state_timeout_seconds = {b.state_timeout_seconds}
connect_timeout_seconds = {b.connect_timeout_seconds}
power_attempts = {b.power_attempts}

[discovery]
mdns = {str(d.mdns).lower()}
subnet_scan = {str(d.subnet_scan).lower()}
mdns_timeout_seconds = {d.mdns_timeout_seconds}
probe_timeout_seconds = {d.probe_timeout_seconds}
subnet = {_toml_string(d.subnet)}

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
    temp = paths.config_file.with_suffix(".toml.tmp")
    temp.write_text(config.to_toml(), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(paths.config_file)
    return paths.config_file
