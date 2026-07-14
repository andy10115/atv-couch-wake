# Changelog

## 0.3.0

- Replaced `androidtvremote2` with an ADB-first control backend.
- Added guided developer-mode, debugging, and standby-power onboarding.
- Added strict `adb` checks without automatic package installation.
- Added optional Wireless debugging pairing-code support.
- Added direct TV Input Framework discovery via `dumpsys tv_input`.
- Added live passthrough-input testing and storage of the exact input URI.
- Added ADB power-on/off and power-cycle tests.
- Kept all lifecycle automation in a per-user systemd watcher.
- Preserved read-only controller USB-root wake diagnostics.
- Removed remote-protocol certificates, pairing, mDNS, and subnet scanning.

## 0.2.0

- Added terminal-first power, input, and USB-root verification.
