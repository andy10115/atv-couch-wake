# atv-couch-wake

atv-couch-wake gives a Linux gaming PC console-like television behavior without an HDMI-CEC adapter.
It talks directly to an Android TV or Google TV over the local network using the same Remote Protocol v2
used by the Google TV mobile remote.

> **Project status:** early alpha. The project now includes terminal-first hardware verification, but power,
> input, suspend, shutdown, and controller wake behavior still need validation across more TVs and PCs.

## What it does

- Turns the TV on when the user's systemd session starts.
- Turns the TV on after resume, retrying while networking returns.
- Turns the TV off before suspend.
- Turns the TV off before shutdown.
- Can keep the TV on during reboot, which is the default.
- Selects HDMI 1, 2, 3, or 4 after wake where the TV firmware supports discrete input commands.
- Guides the user through discovery, pairing, input choice, testing, and service installation.
- Rediscovers a TV when DHCP changes its address, using mDNS first and a bounded LAN scan as fallback.
- Traces each likely controller to its actual USB root hub and parent PCI controller, then reports whether the root is wake-enabled.

The project was inspired by
[`mihirdash108/bazzite-tv-wake`](https://github.com/mihirdash108/bazzite-tv-wake), but restructures the
idea as an installable utility with configuration, a setup wizard, diagnostics, and a lifecycle watcher.

## Requirements

- Linux with systemd and a working per-user systemd manager.
- Python 3.10 or newer.
- Android TV or Google TV with Android TV Remote Service available.
- PC and TV on the same LAN, or firewall rules allowing the connection.
- TCP 6466 for remote commands and TCP 6467 for pairing.
- Network standby / Quick Start enabled on TVs that disable networking while asleep.

Bazzite and other atomic distributions are a primary target. Installation is entirely below the user's
home directory; the utility does not layer Python packages into the immutable base image.

## Install

### One-command install

```bash
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash
```

Pass setup-interface options through Bash like this:

```bash
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash -s -- --terminal
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash -s -- --kdialog
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash -s -- --zenity
```

To install without immediately launching the setup wizard:

```bash
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash -s -- --no-setup
```

The remote installer downloads the current `main` branch from:

```text
https://github.com/andy10115/atv-couch-wake
```

### Install from a checkout

```bash
git clone https://github.com/andy10115/atv-couch-wake.git
cd atv-couch-wake
./install.sh
```

To force a particular setup interface:

```bash
./install.sh --terminal
./install.sh --kdialog
./install.sh --zenity
```

To install without launching the wizard:

```bash
./install.sh --no-setup
```

The installer creates an isolated virtual environment at:

```text
~/.local/share/atv-couch-wake/venv
```

It installs the launcher at:

```text
~/.local/bin/atv-couch-wake
```

After a `--no-setup` installation, start the wizard with:

```bash
atv-couch-wake setup
```

## Guided setup

The wizard:

1. Inspects the Linux environment.
2. Searches for `_androidtvremote2._tcp.local.` mDNS services.
3. Falls back to a bounded local IPv4 subnet scan when necessary.
4. Lets the user enter a TV address manually.
5. Starts pairing and asks for the code displayed by the television.
6. Saves the generated certificate and private key with user-only permissions.
7. Asks which HDMI input the PC uses and sends that exact discrete input command for confirmation.
8. Offers a five-second TV off/on power-cycle test before automation is enabled.
9. Traces detected controllers to their USB root hubs and reports whether each root is armed for wake.
10. Asks which lifecycle events should control the television.
11. Installs and starts a per-user systemd watcher.

Configuration is stored at:

```text
~/.config/atv-couch-wake/config.toml
```

Pairing credentials are stored at:

```text
~/.local/share/atv-couch-wake/cert.pem
~/.local/share/atv-couch-wake/key.pem
```

## Commands

```bash
atv-couch-wake setup
atv-couch-wake pair [TV_IP]
atv-couch-wake on
atv-couch-wake off
atv-couch-wake status
atv-couch-wake input [1-4]
atv-couch-wake diagnose
atv-couch-wake diagnose --json

atv-couch-wake -v test power-on
atv-couch-wake -v test power-off
atv-couch-wake -v test power-cycle
atv-couch-wake -v test input 1
atv-couch-wake -v test input 3 --save
atv-couch-wake test usb-wake
atv-couch-wake test key WAKEUP
atv-couch-wake test key POWER --force

atv-couch-wake service install
atv-couch-wake service remove
atv-couch-wake service status
atv-couch-wake service logs

atv-couch-wake uninstall
atv-couch-wake uninstall --purge --remove-runtime
```

`--purge` deletes configuration and pairing credentials. Without it, uninstalling preserves the TV pairing
so the utility can be reinstalled without pairing again.

## Terminal-first hardware verification

Before enabling the lifecycle service, verify the TV and controller behavior directly:

```bash
atv-couch-wake -v status
atv-couch-wake -v test power-cycle
atv-couch-wake -v test input 1
atv-couch-wake -v test input 2
atv-couch-wake -v test input 3 --save
atv-couch-wake -v test input 4
atv-couch-wake test usb-wake
```

`test power-cycle` reads the TV's reported state, turns it off when safe, waits five seconds, and then tests
power-on. It refuses to issue a blind toggle when the state is unknown. `test input N` sends the exact
`TV_INPUT_HDMI_N` key and asks whether it worked; `--save` stores a confirmed input.

For protocol troubleshooting, raw non-toggle keys can be sent explicitly:

```bash
atv-couch-wake -v test key WAKEUP
atv-couch-wake -v test key SLEEP
atv-couch-wake -v test key TV_INPUT_HDMI_3
```

A raw `POWER` command is intentionally guarded because it is a toggle:

```bash
atv-couch-wake -v test key POWER --force
```

## Lifecycle design

The installed user service runs a small watcher connected to the system D-Bus. It requests a logind delay
inhibitor for sleep and shutdown, then listens for:

- `PrepareForSleep`
- `PrepareForShutdown`
- `PrepareForShutdownWithMetadata`, when supported by the installed systemd version

When sleep or shutdown begins, the watcher sends the TV command and releases the inhibitor. After resume,
it reacquires the inhibitor and wakes the TV. On systemd versions that provide shutdown metadata, reboot
can be distinguished from poweroff. Older versions fall back to treating the event as a normal shutdown.

The delay available to applications is limited by logind's `InhibitDelayMaxSec`. The watcher reads logind's actual delay limit and caps the configured 4.5-second local deadline so a broken TV connection cannot indefinitely block sleep or shutdown.

## Safe power behavior

Power handling avoids blindly sending a toggle:

1. Read the TV's current reported state.
2. Return immediately when it already matches the target.
3. Wait one second after connecting because some TVs silently drop commands sent immediately after the remote session opens.
4. When state is known and opposite to the target, send `POWER`, wait, and re-check before every retry.
5. When state is unknown, never send `POWER`; try the discrete `WAKEUP` or `SLEEP` key once instead.
6. If a command closes the connection, reconnect briefly and verify the requested state where possible.

This matches the proven state-aware approach used by the reference project while retaining the safer discrete
commands for unknown-state cases. Some firmware ignores discrete sleep, wake, or HDMI commands, so setup and
the standalone terminal tests require confirmation before input switching is saved.

## Controller wake diagnostics

Version 0.1 does not automatically edit udev rules or enable/disable wake sources. Run:

```bash
atv-couch-wake diagnose
```

The report lists:

- likely game controller input devices,
- the matching `/sys/class/input/eventN/device` path,
- the controller's USB device node,
- the exact `usbN` root hub and its `power/wakeup` state,
- the parent PCI controller and its `power/wakeup` state,
- a per-controller `READY` or `NOT ARMED` result,
- TV reachability and service state.

The check is still read-only. It verifies whether the root is armed but does not write persistent udev rules
or automatically enable a root hub, because changing the wrong wake source can create immediate/spurious wakes.

## Logs

```bash
journalctl --user -u atv-couch-wake-watcher.service -f
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
pytest
ruff check .
```

The standard-library test suite can also run without third-party dependencies installed:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Known limitations

- Startup means the user's systemd service manager has started, normally after login or automatic login. It
  is not a pre-login system service.
- Reboot detection depends on newer logind metadata. On older systemd versions, shutdown and reboot cannot
  be reliably distinguished by the watcher.
- Network shutdown behavior varies by distribution. The logind inhibitor is intended to run the TV command
  before networking is torn down, but this needs real-system validation.
- TVs may stop listening on the network while asleep unless Quick Start or network standby is enabled.
- A port scan can identify another service listening on 6466. Stored TV name/MAC identity is used during
  rediscovery to reduce the chance of selecting the wrong host.
- HDMI input keycodes are part of Android's remote key enum, but individual television firmware may ignore them.
- Multi-TV profiles are not implemented yet.
- Controller wake configuration remains manual and hardware-specific; the utility now verifies the root path and state but does not alter it.

## Security notes

- The pairing private key is stored with mode `0600`.
- The lifecycle service runs as the logged-in user, not root.
- The setup utility does not automatically modify udev rules, PCI wake settings, or USB wake settings.
- Subnet scans are limited to directly connected IPv4 networks and narrowed to `/24` where necessary.

## License

MIT. The dependency `androidtvremote2` is licensed separately under Apache-2.0.
