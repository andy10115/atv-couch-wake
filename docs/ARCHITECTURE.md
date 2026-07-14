# Architecture

## ADB backend

`adb_control.py` is the single TV-control backend. It resolves the distribution-provided `adb` executable, connects to the configured serial, verifies authorization, and executes commands as the desktop user.

Power uses discrete Android keyevents:

- `KEYCODE_WAKEUP`
- `KEYCODE_SLEEP`

Input switching uses Android's TV Input Framework rather than remote-protocol HDMI keycodes. The backend parses physical passthrough IDs from `dumpsys tv_input`, percent-encodes the selected vendor-specific input ID, and launches its passthrough URI with `am start`.

No manufacturer input numbers are hard-coded. A TCL may expose `HW15` through `com.tcl.tvinput/.TvPassThroughService`; another vendor may expose a different component and hardware numbering scheme.

## Onboarding

`setup_wizard.py` treats live hardware verification as part of configuration:

1. Require an existing `adb` installation.
2. Explain developer-mode and standby-network settings.
3. Support classic TCP debugging and optional Wireless debugging pairing.
4. Wait for ADB authorization.
5. Test sleep and wake without making a failed optional test fatal to the rest of setup.
6. Discover and test physical passthrough inputs while preserving an existing saved input if a rerun cannot reconfirm it.
7. Save the full working input ID and URI.
8. Configure lifecycle policy.
9. Detect likely controllers and trace them to stable USB-root/PCI wake paths.
10. Optionally install a persistent udev rule with one-time administrator authorization.
11. Install the per-user lifecycle watcher independently of controller-wake success.
12. Summarize verified, unverified, skipped, and installed features.
13. Require a reboot before controller-wake testing, then instruct the user to suspend normally after reboot and try the controller.
14. Point to Wake-on-LAN as a manual, distro-specific fallback rather than configuring it automatically.

Terminal, KDialog, and Zenity share the same setup logic through `ui.py`.

## Lifecycle watcher

`lifecycle.py` runs inside `atv-couch-wake-watcher.service`, a per-user systemd unit. It connects to the system logind bus and holds a delay inhibitor for `sleep:shutdown`.

When logind announces an impending suspend or shutdown, the watcher sends the ADB sleep command before releasing the inhibitor. On startup and after resume, it waits five seconds before the first ADB wake attempt so the user session, network, ADB transport, and TV standby services can settle. It then uses the existing retry path and launches the saved input URI.

The watcher is deliberately not a system service:

- It reuses the user's ADB authorization keys under `~/.android`.
- It avoids root-owned scripts and distribution-specific filesystem paths.
- It works on mutable and immutable systemd distributions.

## Controller wake

`diagnostics.py` parses `/proc/bus/input/devices`, follows each likely controller through sysfs, and reports the corresponding USB root hub and PCI wake state.

`controller_wake.py` turns that topology into an optional persistent hardware configuration. The preferred rule targets the selected controller's stable USB root hub and, when available, its parent PCI controller. It deliberately does not match the temporary `eventN` or leaf USB device, so wireless dongles may re-enumerate without losing the wake configuration.

The persistent rule is installed at `/etc/udev/rules.d/90-atv-couch-wake-controller.rules` using one-time `sudo` authorization. This is not a service: no root daemon or system-level systemd unit is created. The TV lifecycle watcher remains a per-user service.

A configurable pre-suspend settle delay can reduce immediate wakeups caused by dongles that re-enumerate as a controller powers off. Because the watcher uses a logind delay inhibitor, that delay is bounded by the system's `InhibitDelayMaxUSec`; the watcher caps the configured delay when necessary.

Controller wake is explicitly treated as best-effort. Onboarding never suspends the machine to test it. A newly written wake rule records the current boot ID and requires a reboot before the manual `controller test` command may suspend the PC. This prevents setup from testing controller wake before the watcher and persistent hardware configuration have started from a clean boot.

When hardware cannot produce a usable wake event, onboarding points users to Wake-on-LAN from a phone as a manually configured fallback. The project does not configure Wake-on-LAN because persistence differs across distributions, network managers, NIC drivers, and firmware. The existing resume/startup watcher still provides the TV wake-and-input-switch behavior after the PC starts.
