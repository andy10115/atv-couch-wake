# atv-couch-wake

`atv-couch-wake` gives a Linux couch-gaming PC console-like control of an Android TV or Google TV:

- TV sleeps before the PC suspends or shuts down.
- TV wakes after the PC resumes or the user session starts.
- The TV switches directly to the saved physical input through Android's TV Input Framework.
- When hardware permits it, a USB controller or wireless controller dongle can wake the PC from suspend.

Version 0.4 uses **ADB exclusively** for TV control and adds optional controller-to-PC wake configuration. It does not rely on HDMI-CEC or Android remote-protocol input keycodes.

Please understand that this means this has not been tested nor will it work with non Android/GoogleTV's.  Other TV manufacturs may have similar functionality, and if you'd like to build on this project to support other TV's such as LG, Samsung, or Roku TV's you may submit a PR or build a standalone tool.

> **Alpha software:** test wake, sleep, input switching, suspend, and shutdown on your own hardware before relying on it.

## Known Limitations

- Not all controllers will support Wake fucntion,  Wake-on-LAN recommended as fallback in these cases, more on this below.
- Some TV manufactures may intentionally block this functionality and it is not possible to test all hardware I do not own.

## Requirements

- Linux with systemd and an active per-user systemd manager.
- Python 3.10 or newer.
- Android TV or Google TV reachable over the local network.
- Android platform tools (`adb`) installed by your distribution.
- Developer options and network/wireless debugging enabled on the TV.
- A trusted local network. ADB is powerful; do not expose its port to the internet.
- **Recommended** - Android TV with a static local IP address. (this is done on your router)

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
5. Never installs a system package or system-wide systemd service. Optional controller wake uses a one-time privileged udev rule, not a root daemon.

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
11. Detects likely USB controllers and wireless controller dongles and traces them to their USB root hub and parent PCI controller.
12. Optionally uses one-time `sudo` authorization to install a persistent udev rule that enables wake on the selected stable hardware path.
13. Offers a real suspend/resume test and records whether controller wake was actually verified.
14. Explains the Wake-on-LAN phone fallback when controller wake is unavailable or fails.
15. Offers to install a **per-user** systemd watcher.

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

## Controller wake

Controller wake is optional and hardware-dependent. Some controller dongles can wake a PC reliably; some cannot, regardless of software configuration. The controller or dongle must emit a real wake event, the USB root hub and parent controller must support wake, and BIOS/UEFI must allow that hardware to resume the machine.

Run guided controller setup:

```bash
atv-couch-wake controller setup
```

The setup flow:

1. Detects likely controllers from Linux input devices.
2. Traces the selected device to its USB root hub and parent PCI USB controller.
3. Enables wake on the stable root-hub path rather than the temporary leaf device. This allows wireless dongles to re-enumerate or change identity without losing the wake configuration.
4. Installs `/etc/udev/rules.d/90-atv-couch-wake-controller.rules` using one-time `sudo` authorization.
5. Applies the setting immediately for the current boot.
6. Optionally adds a short pre-suspend settling delay for dongles that re-enumerate when the controller connects or disconnects.
7. Offers a real suspend test and records whether controller wake was actually verified.

The TV lifecycle watcher remains a **per-user** systemd service. Controller wake does not install a root daemon or a system-level systemd service; only the persistent udev hardware rule is privileged.

Check status and topology:

```bash
atv-couch-wake controller status
atv-couch-wake test usb-wake
```

Retest after changing ports, firmware, BIOS settings, or controllers:

```bash
atv-couch-wake controller test
```

Remove the persistent controller wake rule:

```bash
atv-couch-wake controller disable
```

### Re-enumerating wireless dongles

Some adapters change USB identity when a controller powers on or off. `atv-couch-wake` does not persist wake against the temporary `eventN` or leaf USB device. It arms the stable USB root hub, so the rule remains applicable when the dongle re-enumerates.

A re-enumeration event can also cause an immediate unwanted wake if it happens at the same moment the PC is entering suspend. The optional settle delay gives the dongle a brief chance to finish that transition first. Because the lifecycle watcher is intentionally a user service, the delay is bounded by logind's delay-inhibitor window and may be capped automatically.

Enabling a USB root hub can also allow other wake-capable devices attached to the same root hub to wake the PC. The wizard prefers a detected controller path and offers an all-root fallback only when selective detection is unavailable.

### When controller wake simply will not work

This is an unavoidable hardware limitation on some systems. A controller may work perfectly once Linux is running but still be unable to generate the USB wake event required to resume the PC. BIOS/UEFI options, USB-controller behavior, dongle revisions, and suspend mode can all matter.

In that case, **Wake-on-LAN from a phone is the best fallback**. You still get the same TV behavior: the phone wakes the PC, then `atv-couch-wake` sees the resume/startup event, wakes the TV, and switches to the gaming input. The only difference is that the first wake comes from the phone instead of the controller.

Quick setup summary:

**Bazzite users can enable Wake-on-Lan from the Bazzite Portal app or built in ujust command ```ujust toggle-wol``` and select the Enable, then Force Enable option.***

**It is recommended to assign your PC a static IP address via your router or appropirate network configurations when using Wake-on-LAN**

1. Prefer wired Ethernet and enable **Wake-on-LAN**, **PCIe wake**, or the equivalent option in BIOS/UEFI.
2. Check the Ethernet interface with `ethtool <interface>` and look for `Supports Wake-on: g` and `Wake-on: g`.
3. NetworkManager users can persist magic-packet wake with:

   ```bash
   nmcli connection modify "<connection name>" 802-3-ethernet.wake-on-lan magic
   ```

   Reconnect the connection or reboot afterward.
4. Install a reputable Wake-on-LAN app on the phone and add the PC's Ethernet MAC address. The simplest setup keeps the phone on the same LAN.
5. Send the magic packet from the phone. The PC wakes; `atv-couch-wake` then wakes the TV and selects the saved input.

**System Services can aslo be used to persist Wake-on-LAN as well. Consult your distros documentation to determine the best method.***

**Wake-on-LAN via wifi WoWLAN is availabe on some chipsets, but is far less universal than WoL, please research if this is possible with your configuration**

Print this guide at any time:

```bash
atv-couch-wake controller wol
```

Do not expose ADB or Wake-on-LAN directly to the public internet. Use a VPN into the home network for remote access.

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
- Saved controller-wake configuration and verification state.
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

The privileged controller-wake udev rule is managed separately so removing the user application cannot silently require sudo. Remove it first with:

```bash
atv-couch-wake controller disable
```

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
