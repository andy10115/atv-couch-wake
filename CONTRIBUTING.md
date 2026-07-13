# Contributing

Real hardware reports are especially valuable. Include:

- distribution and systemd version,
- TV manufacturer/model and Android/Google TV version,
- whether network standby or Quick Start is enabled,
- wired or wireless TV networking,
- whether `WAKEUP`, `SLEEP`, and discrete HDMI input commands work,
- relevant `journalctl --user -u atv-couch-wake-watcher.service` output,
- controller dongle name and the `Phys` line from `atv-couch-wake diagnose`.

Do not publish pairing certificate or private-key contents.

Before opening a pull request:

```bash
python -m pip install -e ".[test]"
ruff check .
pytest
```
