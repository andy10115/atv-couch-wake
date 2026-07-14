# Contributing

Real hardware reports are especially valuable. Include:

- distribution and systemd version,
- `adb version` output and how Android platform tools were installed,
- TV manufacturer/model and Android/Google TV version,
- the debugging mode used (classic network debugging or Wireless debugging),
- whether Optimized energy mode, Quick Start, Quick Resume, or Network Standby is enabled,
- wired or wireless TV networking,
- redacted `dumpsys tv_input` passthrough IDs,
- whether `KEYCODE_WAKEUP`, `KEYCODE_SLEEP`, and direct passthrough URI launches work,
- relevant `journalctl --user -u atv-couch-wake-watcher.service` output,
- controller dongle name and the USB-root section from `atv-couch-wake diagnose`.

Do not publish `~/.android/adbkey`, pairing codes, private IP details you do not wish to share, or other credentials.

Before opening a pull request:

```bash
python -m pip install -e ".[test]"
ruff check .
ruff format --check .
pytest
```
