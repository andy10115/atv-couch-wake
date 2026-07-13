# Architecture

## Components

### CLI

`atv_couch_wake.cli` exposes the `atv-couch-wake` command. It keeps automation events and interactive
commands in the same executable so systemd does not need generated shell scripts containing usernames or
hard-coded home-directory paths.

### Configuration and paths

`paths.py` resolves XDG locations. `config.py` maps TOML into dataclasses and writes updates atomically.
Certificates live in the XDG data directory rather than the configuration directory because they are
persistent application data.

### Discovery

`discovery.py` uses three discovery paths:

1. Android TV Remote v2 mDNS service discovery.
2. Direct use of the configured host.
3. A bounded scan of directly attached IPv4 networks for TCP 6466.

Runtime rediscovery compares the TV certificate name or MAC against the identity stored during pairing.

### Pairing and remote control

`pairing.py` wraps the `androidtvremote2` certificate and PIN flow. `remote.py` contains state-aware power,
status, input, reconnect, and rediscovery behavior.

The remote factory is injectable so tests do not need a television.

### UI

`ui.py` provides one wizard API with terminal, KDialog, and Zenity implementations. Business logic does not
contain desktop-specific subprocess calls.

### Lifecycle watcher

`lifecycle.py` connects to `org.freedesktop.login1` on the system bus and requests a delay inhibitor for
`sleep:shutdown`. The watcher handles startup immediately, then responds to sleep/resume and
shutdown/reboot signals.

The inhibitor file descriptor is closed only after the relevant TV command completes or reaches the local
deadline. It is reacquired after resume.

### systemd integration

`systemd_integration.py` creates a per-user service using the exact Python interpreter running the setup
command. This allows `install.sh` to use a home-directory virtual environment and works on atomic systems.

### Diagnostics

`diagnostics.py` is deliberately read-only. It parses `/proc/bus/input/devices` and reads sysfs wake files,
but never writes to them.

## Privilege model

No component requires root for normal installation or operation. The logged-in user can access the system
D-Bus login1 interface and request delay inhibitors. USB wake configuration is outside the first release.

## Event policy

| Event | Default |
| --- | --- |
| User service startup | TV on, select configured HDMI input |
| Resume | TV on, select configured HDMI input |
| Suspend | TV off |
| Poweroff | TV off |
| Reboot | Leave TV on |

## Future work

- Hardware-tested USB wake rule generation.
- Multiple TV profiles.
- Gaming-session-only startup activation.
- Vendor-specific input selection fallbacks.
- A proper packaged release through PyPI or distro/community repositories.
- Structured journald fields and automated support bundles.
