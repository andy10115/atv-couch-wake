#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="atv-couch-wake"
REPO_SLUG="andy10115/atv-couch-wake"
REPO_URL="https://github.com/$REPO_SLUG"
ARCHIVE_URL="$REPO_URL/archive/refs/heads/main.tar.gz"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/$APP_NAME"
VENV="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SOURCE_DIR=""
TEMP_SOURCE=""

cleanup() {
  if [[ -n "$TEMP_SOURCE" && -d "$TEMP_SOURCE" ]]; then
    rm -rf -- "$TEMP_SOURCE"
  fi
}
trap cleanup EXIT

NO_SETUP=0
UI="auto"
for arg in "$@"; do
  case "$arg" in
    --no-setup) NO_SETUP=1 ;;
    --terminal) UI="terminal" ;;
    --kdialog) UI="kdialog" ;;
    --zenity) UI="zenity" ;;
    -h|--help)
      cat <<HELP
Usage: ./install.sh [--no-setup] [--terminal|--kdialog|--zenity]

Installs atv-couch-wake into an isolated virtual environment under:
  $VENV

This script can be run from a cloned repository or piped directly from:
  https://raw.githubusercontent.com/$REPO_SLUG/main/install.sh
HELP
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

if ! command -v adb >/dev/null 2>&1; then
  cat >&2 <<'ADBHELP'
ADB (Android platform tools) is required but was not found.

Install it with your distribution's supported package method, then rerun this installer:

  Arch Linux / CachyOS:  sudo pacman -S android-tools
  Fedora:                sudo dnf install android-tools
  Debian / Ubuntu:       sudo apt install adb
  openSUSE:              sudo zypper install android-tools
  Bazzite:               use Bazzite's supported ujust/portal recipe for Android platform tools

atv-couch-wake does not install or layer system packages automatically.
ADBHELP
  exit 1
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required; found {sys.version.split()[0]}")
PY

# When run from a checkout, install that checkout. When piped from GitHub,
# download the matching main-branch source archive into a temporary directory.
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != "bash" && "$SCRIPT_SOURCE" != "/dev/stdin" ]]; then
  candidate="$(cd -- "$(dirname -- "$SCRIPT_SOURCE")" 2>/dev/null && pwd || true)"
  if [[ -f "$candidate/pyproject.toml" ]]; then
    SOURCE_DIR="$candidate"
  fi
fi

if [[ -z "$SOURCE_DIR" ]]; then
  command -v curl >/dev/null || {
    echo "curl is required when install.sh is run without a local repository checkout" >&2
    exit 1
  }
  command -v tar >/dev/null || {
    echo "tar is required when install.sh is run without a local repository checkout" >&2
    exit 1
  }
  TEMP_SOURCE="$(mktemp -d -t atv-couch-wake.XXXXXX)"
  echo "Downloading $REPO_URL ..."
  curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$TEMP_SOURCE" --strip-components=1
  SOURCE_DIR="$TEMP_SOURCE"
fi

if [[ ! -f "$SOURCE_DIR/pyproject.toml" ]]; then
  echo "Could not locate the atv-couch-wake source tree." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV" || {
    echo "Could not create a virtual environment. Install your distribution's python3-venv package." >&2
    exit 1
  }
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --upgrade "$SOURCE_DIR"
ln -sfn "$VENV/bin/atv-couch-wake" "$BIN_DIR/atv-couch-wake"

# A running watcher has the old Python modules loaded in memory. Restart it after
# an upgrade so --no-setup installations immediately use the new code.
if command -v systemctl >/dev/null 2>&1 \
  && systemctl --user is-active --quiet atv-couch-wake-watcher.service 2>/dev/null; then
  systemctl --user restart atv-couch-wake-watcher.service || \
    echo "Warning: installed the update but could not restart the user watcher." >&2
fi

cat <<DONE
Installed atv-couch-wake.
Launcher: $BIN_DIR/atv-couch-wake
Repository: $REPO_URL

Make sure $BIN_DIR is in your PATH.
DONE

if [[ "$NO_SETUP" -eq 0 ]]; then
  exec "$BIN_DIR/atv-couch-wake" setup --ui "$UI"
fi
