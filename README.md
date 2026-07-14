# atv-couch-wake

`atv-couch-wake` gives a Linux couch-gaming PC console-like control of an Android TV or Google TV:

- TV sleeps before the PC suspends or shuts down.
- TV wakes after the PC resumes or the user session starts.
- The TV switches directly to the saved physical input through Android's TV Input Framework.
- Controller USB-root wake state is checked and reported without changing hardware settings.

Version 0.3 uses **ADB exclusively** for TV control. It does not rely on HDMI-CEC or Android remote-protocol input keycodes.

Please understand that this means this has not been tested nor will it work with non Android/GoogleTV's.  Other TV manufacturs may have similar functionality, and if you'd like to build on this project to support other TV's such as LG, Samsung, or Roku TV's you may submit a PR or build a standalone tool.

> **Alpha software:** test wake, sleep, input switching, suspend, and shutdown on your own hardware before relying on it.

## Requirements

- Linux with systemd and an active per-user systemd manager.
- Python 3.10 or newer.
- Android TV or Google TV reachable over the local network.
- Android TV with a static local IP address. (this is done in your router)
- Android platform tools (`adb`) installed by your distribution.
- Developer options and network/wireless debugging enabled on the TV.
- A trusted local network. ADB is powerful; do not expose its port to the internet.

### Install ADB first

The installer checks for `adb` and stops with instructions when it is missing. It deliberately does not layer or install system packages.

```bash
# Arch Linux / CachyOS
sudo pacman -S android-tools

# Fedora
sudo dnf install android-tools

# Debian / Ubuntu
sudo apt install adb

# openSUSE
sudo zypper install android-tools
```

On Bazzite, use Bazzite's supported `ujust` recipe or portal option for Android platform tools before running the installer.

Verify:

```bash
adb version
```

## TV preparation

The setup wizard shows these steps, but doing them first is easier.

### 1. Enable developer options

Menu names vary slightly by TV manufacturer:

1. Open **Settings → System → About**.
2. Highlight **Android TV OS build**.
3. Press **OK/Select seven times** until the TV reports that developer mode is enabled.
4. Return to **Settings → System → Developer options**.
5. Enable **USB debugging**, **Network debugging**, or **Wireless debugging**, depending on what the TV exposes.
6. Accept the warning.

Classic network debugging normally uses TCP port `5555`. Newer Wireless debugging screens may show a separate connection port and an optional pairing-code workflow; the setup wizard supports both.

### 2. Keep networking alive in standby

Open **Settings → System → Power & Energy** or the equivalent power menu:

1. Set **Energy mode / Energy Saver** to **Optimized** when available.
2. Enable **Quick Start**, **Quick Resume**, **Fast TV Start**, or **Network Standby**—whichever name your TV uses.
3. Avoid an aggressive Eco/Low-power mode that fully turns off networking while the panel is off.

ADB cannot wake a TV whose network and Android services are completely shut down.

### 3. Find the TV's IP address

Look under **Settings → Network & Internet**. A DHCP reservation is recommended, though the IP can be changed later by rerunning setup.

## Installation

From GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash
```

Terminal-only setup:

```bash
curl -fsSL https://raw.githubusercontent.com/andy10115/atv-couch-wake/main/install.sh | bash -s -- --terminal
```

From a clone:

```bash
git clone https://github.com/andy10115/atv-couch-wake.git
cd atv-couch-wake
./install.sh
```

The installer:

1. Checks for Python 3.10+ and `adb`.
2. Creates an isolated virtual environment under `~/.local/share/atv-couch-wake/venv`.
3. Installs the CLI at `~/.local/bin/atv-couch-wake`.
4. Starts the guided setup unless `--no-setup` is supplied.
5. Never installs a system package or system-wide service.

## Guided onboarding

Run or rerun:

```bash
atv-couch-wake setup --ui terminal
```

The wizard:

1. Confirms `adb` is present and records its full path for the user service.
2. Walks through developer mode and standby-power settings.
3. Connects to the TV and waits for the debugging authorization prompt.
4. Optionally runs `adb pair` for TVs using Wireless debugging pairing codes.
5. Verifies authorization and reads the TV model.
6. Tests `KEYCODE_SLEEP` and `KEYCODE_WAKEUP` interactively.
7. Reads `dumpsys tv_input` and extracts physical passthrough inputs.
8. Launches each input directly and asks which one displays the gaming PC.
9. Stores the exact vendor-specific input ID and passthrough URI.
10. Asks which startup, suspend, resume, shutdown, and reboot behaviors to enable.
11. Reports whether each detected controller's actual USB root hub is armed for wake.
12. Offers to install a **per-user** systemd watcher.

The TV will display an authorization prompt during the first connection. Select **Always allow from this computer** before accepting it.

## Manual verification

These commands use the same ADB backend as the lifecycle watcher.

```bash
atv-couch-wake status
atv-couch-wake test power-off
atv-couch-wake test power-on
atv-couch-wake test power-cycle
```

List all physical passthrough inputs:

```bash
atv-couch-wake inputs
```

Test every discovered input interactively and save the one connected to the PC:

```bash
atv-couch-wake test inputs
```

Test a specific hardware ID without saving it:

```bash
atv-couch-wake test input HW15
```

Select the saved input:

```bash
atv-couch-wake input
```

Select a specific discovered input:

```bash
atv-couch-wake input HW17
```

Send a raw Android keyevent:

```bash
atv-couch-wake test key KEYCODE_HOME
```

## Direct ADB equivalents

Power off:

```bash
adb -s TV_IP:5555 shell input keyevent KEYCODE_SLEEP
```

Power on:

```bash
adb connect TV_IP:5555
adb -s TV_IP:5555 shell input keyevent KEYCODE_WAKEUP
```

Inspect inputs:

```bash
adb -s TV_IP:5555 shell dumpsys tv_input
```

A TCL input may look like:

```text
com.tcl.tvinput/.TvPassThroughService/HW15
```

`atv-couch-wake` converts it to a URI such as:

```text
content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2FHW15
```

and launches it with:

```bash
adb -s TV_IP:5555 shell am start \
  -a android.intent.action.VIEW \
  -d 'content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2FHW15'
```

The setup wizard discovers and tests this value; it does not assume that `HW15` means a particular HDMI port on every TV.

## User systemd service

All lifecycle automation runs as the current user. No files are installed under `/etc/systemd/system`, and no root-owned sleep hooks are created.

Install or refresh the watcher:

```bash
atv-couch-wake service install
```

Status:

```bash
atv-couch-wake service status
```

Logs:

```bash
atv-couch-wake service logs
```

Remove it:

```bash
atv-couch-wake service remove
```

The unit is installed at:

```text
~/.config/systemd/user/atv-couch-wake-watcher.service
```

The watcher connects to the system logind D-Bus API, holds a delay inhibitor, and listens for suspend/resume and shutdown/reboot signals. This lets the ADB sleep command run before network teardown while remaining a distro-agnostic user service.

The user must have an active systemd user session. Couch-oriented distributions that automatically log into Gaming Mode satisfy this naturally.

## Controller wake diagnostics

```bash
atv-couch-wake test usb-wake
```

For each likely controller, the report traces:

```text
input event → USB device → USB root hub → PCI controller
```

It explicitly reports the root hub as `READY` or `NOT ARMED`. The check is read-only; this release does not write udev rules or enable every root hub automatically.

## Configuration

Configuration is stored at:

```text
~/.config/atv-couch-wake/config.toml
```

The saved ADB executable is an absolute path so the user service does not depend on an interactive shell's `PATH`.

See [`docs/config.example.toml`](docs/config.example.toml).

## Diagnostics

```bash
atv-couch-wake diagnose
atv-couch-wake diagnose --json
```

The report includes:

- ADB availability and configured path.
- TV reachability, authorization, power state, model, and current input.
- User-service installation and runtime status.
- Controller-to-root-hub wake topology.
- All USB and PCI wake-enabled entries.

## Uninstall

Remove only automation:

```bash
atv-couch-wake uninstall
```

Remove automation and local configuration:

```bash
atv-couch-wake uninstall --purge
```

Remove the managed virtual environment too:

```bash
atv-couch-wake uninstall --purge --remove-runtime
```

The uninstaller intentionally leaves `~/.android/adbkey*` alone because those keys may be used by other Android devices and tools. Revoke the computer from the TV's Developer options when desired.

## Security

ADB authorization gives this computer substantial control over the TV.

- Use it only on a trusted LAN.
- Do not forward or expose the TV's ADB port to the internet.
- Select **Always allow** only on machines you control.
- Revoke debugging authorizations from the TV if the PC is retired or compromised.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
ruff check .
ruff format --check .
pytest
```

## License

MIT.
