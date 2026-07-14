# Changelog

## 0.4.0

- Added optional guided controller-to-PC wake configuration.
- Added controller selection and tracing to stable USB root-hub and PCI wake paths.
- Added a persistent udev rule installed with one-time `sudo` authorization; TV automation remains a per-user systemd service.
- Added selective root-hub wake configuration plus an explicit all-root fallback.
- Added handling for wireless dongles that re-enumerate by persisting against stable root topology rather than temporary leaf devices.
- Added an optional bounded pre-suspend settle delay for re-enumeration-related immediate wakeups.
- Added a real suspend/controller-wake verification test and persisted verified/unverified state.
- Added `controller setup`, `controller status`, `controller test`, `controller disable`, and `controller wol` commands.
- Added a Wake-on-LAN phone fallback guide for hardware that cannot wake from a controller.
- Expanded diagnostics and configuration with saved controller-wake state.

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
