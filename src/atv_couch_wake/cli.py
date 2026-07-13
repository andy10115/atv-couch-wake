"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .config import load_config, save_config
from .diagnostics import collect_diagnostics, diagnostics_json, render_diagnostics
from .lifecycle import LogindWatcher, handle_event
from .pairing import pair_device
from .paths import AppPaths
from .remote import PairingRequired, TVControlError, TVController
from .setup_wizard import run_setup
from .systemd_integration import (
    install_user_service,
    remove_user_service,
    service_status,
)
from .ui import UserCancelled, select_ui

LOGGER = logging.getLogger("atv_couch_wake")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atv-couch-wake",
        description="Control an Android TV / Google TV as part of a Linux console setup.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="run the guided setup wizard")
    setup.add_argument("--ui", choices=["auto", "terminal", "kdialog", "zenity"], default="auto")
    setup.add_argument("--host", default="", help="skip discovery and use this TV address")
    setup.add_argument("--no-service", action="store_true", help="do not offer to install automation")

    pair = sub.add_parser("pair", help="pair or re-pair with a TV")
    pair.add_argument("host", nargs="?", default="", help="TV IP address; defaults to configured host")
    pair.add_argument("--ui", choices=["auto", "terminal", "kdialog", "zenity"], default="auto")

    sub.add_parser("on", help="turn the TV on safely")
    sub.add_parser("off", help="turn the TV off safely")
    sub.add_parser("status", help="show current TV state")

    input_parser = sub.add_parser("input", help="select a discrete HDMI input")
    input_parser.add_argument("number", nargs="?", type=int, choices=[1, 2, 3, 4])

    diagnose = sub.add_parser("diagnose", help="show read-only hardware and service diagnostics")
    diagnose.add_argument("--json", action="store_true", help="print machine-readable JSON")

    service = sub.add_parser("service", help="manage the per-user lifecycle watcher")
    service.add_argument("action", choices=["install", "remove", "status", "logs"])

    event = sub.add_parser("event", help="run one lifecycle event (mainly for testing)")
    event.add_argument("name", choices=["startup", "resume", "suspend", "shutdown", "reboot"])

    sub.add_parser("watcher", help="run the logind lifecycle watcher in the foreground")

    uninstall = sub.add_parser("uninstall", help="remove service integration and optionally user data")
    uninstall.add_argument("--purge", action="store_true", help="also delete pairing keys and config")
    uninstall.add_argument(
        "--remove-runtime",
        action="store_true",
        help="remove the install.sh-managed virtual environment after this command exits",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _pair_command(args: argparse.Namespace, paths: AppPaths) -> int:
    config = load_config(paths, required=False)
    host = args.host or config.tv.host
    if not host:
        raise TVControlError("No TV address supplied. Run 'atv-couch-wake setup' or pass an IP address.")
    ui = select_ui(args.ui)
    paired = await pair_device(host, config, ui, paths)
    config.tv.host = paired.host
    config.tv.name = paired.name
    config.tv.mac = paired.mac
    save_config(config, paths)
    ui.info("Pairing complete", f"Paired with {paired.name} at {paired.host}.")
    return 0


async def _tv_command(args: argparse.Namespace, paths: AppPaths) -> int:
    config = load_config(paths)
    controller = TVController(config, paths)
    if args.command == "on":
        result = await controller.set_power(True)
        print(result.message)
        return 0 if result.success else 1
    if args.command == "off":
        result = await controller.set_power(False)
        print(result.message)
        return 0 if result.success else 1
    if args.command == "status":
        status = await controller.status()
        state = "on" if status.is_on is True else "off" if status.is_on is False else "unknown"
        print(f"TV: {config.tv.name or 'Android TV'}")
        print(f"Host: {status.host}")
        print(f"Power: {state}")
        print(f"Current app: {status.current_app or 'unknown'}")
        if status.device_info is not None:
            print(f"Device: {status.device_info}")
        return 0
    if args.command == "input":
        selected = args.number or config.tv.hdmi_input
        await controller.select_input(selected)
        print(f"Sent HDMI {selected} input command.")
        return 0
    raise ValueError(args.command)


async def _run_async(args: argparse.Namespace, paths: AppPaths) -> int:
    if args.command == "setup":
        ui = select_ui(args.ui)
        await run_setup(ui, host_override=args.host, install_service=not args.no_service, paths=paths)
        return 0
    if args.command == "pair":
        return await _pair_command(args, paths)
    if args.command in {"on", "off", "status", "input"}:
        return await _tv_command(args, paths)
    if args.command == "diagnose":
        report = await collect_diagnostics(paths)
        print(diagnostics_json(report) if args.json else render_diagnostics(report))
        return 0
    if args.command == "event":
        result = await handle_event(args.name, paths=paths)
        print(result.message)
        return 0 if result.success else 1
    if args.command == "watcher":
        config = load_config(paths)
        await LogindWatcher(config, paths).run()
        return 0
    raise ValueError(args.command)


def _service_command(action: str, paths: AppPaths) -> int:
    if action == "install":
        path = install_user_service(paths)
        print(f"Installed and started {path}")
        return 0
    if action == "remove":
        remove_user_service(paths)
        print("Removed the per-user lifecycle watcher.")
        return 0
    if action == "status":
        status = service_status(paths)
        print(f"installed: {status.installed}")
        print(f"enabled: {status.enabled}")
        print(f"active: {status.active}")
        print(status.detail)
        return 0 if status.active else 1
    if action == "logs":
        return subprocess.call(["journalctl", "--user", "-u", "atv-couch-wake-watcher.service", "-f"])
    raise ValueError(action)


def _schedule_runtime_removal(paths: AppPaths) -> bool:
    managed_venv = paths.data_dir / "venv"
    try:
        same_runtime = Path(sys.prefix).resolve() == managed_venv.resolve()
    except OSError:
        same_runtime = False
    if not same_runtime:
        return False
    launcher = Path.home() / ".local/bin/atv-couch-wake"
    subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            'sleep 1; rm -rf -- "$1"; rm -f -- "$2"',
            "atv-couch-wake-uninstall",
            str(managed_venv),
            str(launcher),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def _uninstall(args: argparse.Namespace, paths: AppPaths) -> int:
    remove_user_service(paths)
    if args.purge:
        shutil.rmtree(paths.config_dir, ignore_errors=True)
        for file in (paths.cert_file, paths.key_file):
            file.unlink(missing_ok=True)
        # Preserve the managed venv until process exit, but remove other state now.
        shutil.rmtree(paths.state_dir, ignore_errors=True)
        shutil.rmtree(paths.runtime_dir, ignore_errors=True)
    scheduled = args.remove_runtime and _schedule_runtime_removal(paths)
    print("Removed systemd integration.")
    if args.purge:
        print("Removed configuration, state, and pairing credentials.")
    if scheduled:
        print("The install.sh-managed runtime and launcher will be removed after this command exits.")
    elif args.remove_runtime:
        print("Runtime was not installed in the managed location; remove it with its package manager.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    paths = AppPaths.from_environment()

    try:
        if args.command == "service":
            return _service_command(args.action, paths)
        if args.command == "uninstall":
            return _uninstall(args, paths)
        return asyncio.run(_run_async(args, paths))
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except UserCancelled:
        print("Cancelled.", file=sys.stderr)
        return 2
    except (TVControlError, PairingRequired, FileNotFoundError, RuntimeError) as exc:
        LOGGER.debug("Command failed", exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
