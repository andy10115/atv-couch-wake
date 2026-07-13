#!/usr/bin/env bash
set -Eeuo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/atv-couch-wake"
LAUNCHER="$HOME/.local/bin/atv-couch-wake"
PURGE=""

if [[ "${1:-}" == "--purge" ]]; then
  PURGE="--purge"
fi

if [[ -x "$LAUNCHER" ]]; then
  "$LAUNCHER" uninstall $PURGE --remove-runtime
else
  systemctl --user disable --now atv-couch-wake-watcher.service 2>/dev/null || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/atv-couch-wake-watcher.service"
  systemctl --user daemon-reload 2>/dev/null || true
  rm -rf "$INSTALL_DIR"
  rm -f "$LAUNCHER"
  if [[ -n "$PURGE" ]]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/atv-couch-wake"
    rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/atv-couch-wake"
  fi
  echo "atv-couch-wake removed."
fi
