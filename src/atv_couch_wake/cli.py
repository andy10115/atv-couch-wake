"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .adb_control import (
    ADBController,
    ADBNotInstalled,
    TVControlError,
    installation_help,
)
from .config import ControllerWakeConfig, load_config, save_config
from .controller_wake import (
    controller_wake_reboot_required,
    remove_wake_configuration,
    wol_fallback_summary,
)
from .diagnostics import collect_diagnostics, diagnostics_json, render_controller_wake, render_diagnostics
from .lifecycle import LogindWatcher, handle_event
from .paths import AppPaths
from .setup_wizard import run_controller_wake_setup, run_setup, test_controller_wake
from .systemd_integration import install_user_service, remove_user_service, service_status
from .ui import UserCancelled, select_ui

LOGGER = logging.getLogger("atv_couch_wake")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atv-couch-wake",
        description="ADB-powered Android TV / Google TV automation for a Linux couch gaming PC.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="run the guided ADB setup wizard")
    setup.add_argument("--ui", choices=["auto", "terminal", "kdialog", "zenity"], default="auto")
    setup.add_argument("--host", default="", help="use this TV address instead of prompting")
    setup.add_argument("--no-service", action="store_true", help="do not offer to install automation")

    sub.add_parser("on", help="wake the TV with KEYCODE_WAKEUP")
    sub.add_parser("off", help="sleep the TV with KEYCODE_SLEEP")
    sub.add_parser("status", help="show ADB, power, and current-input state")
    sub.add_parser("inputs", help="list passthrough inputs exposed by the TV Input Framework")

    input_parser = sub.add_parser("input", help="select the saved input or a specific HW/input ID")
    input_parser.add_argument("target", nargs="?", default="", help="HW15, full input ID, or passthrough URI")

    diagnose = sub.add_parser("diagnose", help="show read-only TV, service, and USB wake diagnostics")
    diagnose.add_argument("--json", action="store_true", help="print machine-readable JSON")

    test = sub.add_parser("test", help="run terminal-first hardware verification")
    test_sub = test.add_subparsers(dest="test_name", required=True)
    test_sub.add_parser("power-on", help="test KEYCODE_WAKEUP")
    test_sub.add_parser("power-off", help="test KEYCODE_SLEEP")
    power_cycle = test_sub.add_parser("power-cycle", help="sleep, wait, then wake the TV")
    power_cycle.add_argument("--off-seconds", type=float, default=5.0)
    test_sub.add_parser("inputs", help="test discovered passthrough inputs and save the PC input")
    test_input = test_sub.add_parser("input", help="test a specific HW/input ID without saving it")
    test_input.add_argument("target", help="HW15, full input ID, or passthrough URI")
    test_sub.add_parser("usb-wake", help="show each controller's USB root wake state")
    test_key = test_sub.add_parser("key", help="send one raw Android keyevent through ADB")
    test_key.add_argument("key_code", help="for example KEYCODE_HOME or KEYCODE_WAKEUP")

    controller = sub.add_parser("controller", help="configure and test controller wake")
    controller_sub = controller.add_subparsers(dest="controller_action", required=True)
    controller_setup = controller_sub.add_parser("setup", help="configure persistent controller wake")
    controller_setup.add_argument("--ui", choices=["auto", "terminal", "kdialog", "zenity"], default="auto")
    controller_test = controller_sub.add_parser("test", help="run a post-reboot suspend/controller wake test")
    controller_test.add_argument("--ui", choices=["auto", "terminal", "kdialog", "zenity"], default="auto")
    controller_sub.add_parser("status", help="show saved controller wake configuration and topology")
    controller_sub.add_parser("disable", help="remove the persistent controller wake udev rule")
    controller_sub.add_parser("wol", help="show the Wake-on-LAN fallback guide")

    service = sub.add_parser("service", help="manage the per-user lifecycle watcher")
    service.add_argument("action", choices=["install", "remove", "status", "logs"])

    event = sub.add_parser("event", help="run one lifecycle event manually")
    event.add_argument("name", choices=["startup", "resume", "suspend", "shutdown", "reboot"])

    sub.add_parser("watcher", help="run the logind lifecycle watcher in the foreground")

    uninstall = sub.add_parser("uninstall", help="remove user-service integration and optional user data")
    uninstall.add_argument("--purge", action="store_true", help="also delete configuration and state")
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


async def _resolve_input(controller: ADBController, target: str) -> tuple[str, str, str]:
    if target.startswith("content://"):
        return target, target, target
    inputs = await controller.discover_inputs()
    needle = target.casefold()
    for candidate in inputs:
        if candidate.hardware_id.casefold() == needle or candidate.input_id.casefold() == needle:
            return candidate.uri, candidate.input_id, candidate.hardware_id
    raise TVControlError(
        f"No passthrough input matched '{target}'. Run 'atv-couch-wake inputs' to list valid IDs."
    )


async def _tv_command(args: argparse.Namespace, paths: AppPaths) -> int:
    config = load_config(paths)
    controller = ADBController(config)
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
        print(f"TV: {config.tv.name or status.model or 'Android TV'}")
        print(f"ADB serial: {status.serial}")
        print(f"Authorized: {status.authorized}")
        print(f"Power: {state}")
        print(f"Current input: {status.current_input_id or 'unknown'}")
        print(f"Saved input: {config.tv.input_label or config.tv.input_id or 'none'}")
        return 0
    if args.command == "inputs":
        inputs = await controller.discover_inputs()
        current = await controller.current_input()
        if not inputs:
            print("No physical passthrough inputs were found.")
            return 1
        for candidate in inputs:
            marker = "*" if candidate.input_id == current else " "
            print(f"{marker} {candidate.hardware_id}: {candidate.input_id}")
            print(f"    {candidate.uri}")
        return 0
    if args.command == "input":
        if not args.target:
            await controller.select_input()
            print(f"Selected {config.tv.input_label or config.tv.input_id}.")
            return 0
        uri, input_id, label = await _resolve_input(controller, args.target)
        await controller.select_input(uri)
        print(f"Selected {label}: {input_id}")
        return 0
    raise ValueError(args.command)


async def _interactive_input_test(controller: ADBController, paths: AppPaths) -> int:
    config = load_config(paths)
    inputs = await controller.discover_inputs()
    if not inputs:
        print("No passthrough inputs were discovered in dumpsys tv_input.")
        return 1
    print("Use the physical remote to open Google TV Home or switch away from this PC.")
    for candidate in inputs:
        answer = input(f"\nTest {candidate.hardware_id} ({candidate.input_id})? [Y/n] ").strip().casefold()
        if answer in {"n", "no"}:
            continue
        await controller.select_input(candidate.uri)
        worked = input("Did this switch to the gaming PC? [y/N] ").strip().casefold() in {"y", "yes"}
        if worked:
            label = input(f"Friendly label [{candidate.hardware_id}]: ").strip() or candidate.hardware_id
            config.tv.input_id = candidate.input_id
            config.tv.input_uri = candidate.uri
            config.tv.input_label = label
            config.behavior.switch_input_after_wake = True
            save_config(config, paths)
            print(f"Saved {label} as the automatic PC input.")
            return 0
    print("No input was saved.")
    return 1


async def _test_command(args: argparse.Namespace, paths: AppPaths) -> int:
    if args.test_name == "usb-wake":
        report = await collect_diagnostics(paths)
        print(render_controller_wake(report))
        wake_paths = report.get("controller_wake_paths", [])
        return 0 if wake_paths and all(item["root_armed"] for item in wake_paths) else 1

    config = load_config(paths)
    controller = ADBController(config)
    if args.test_name == "power-on":
        result = await controller.set_power(True)
        print(result.message)
        return 0 if result.success else 1
    if args.test_name == "power-off":
        result = await controller.set_power(False)
        print(result.message)
        return 0 if result.success else 1
    if args.test_name == "power-cycle":
        off = await controller.set_power(False)
        print(off.message)
        if not off.success:
            return 1
        delay = max(1.0, args.off_seconds)
        print(f"Waiting {delay:g} seconds...")
        await asyncio.sleep(delay)
        on = await controller.set_power(True)
        print(on.message)
        return 0 if on.success else 1
    if args.test_name == "inputs":
        return await _interactive_input_test(controller, paths)
    if args.test_name == "input":
        uri, input_id, label = await _resolve_input(controller, args.target)
        await controller.select_input(uri)
        print(f"Sent direct TV Input Framework launch for {label}: {input_id}")
        return 0
    if args.test_name == "key":
        key = args.key_code.strip().upper()
        await controller.shell("input", "keyevent", key)
        print(f"Sent {key} through ADB.")
        return 0
    raise ValueError(args.test_name)


async def _run_async(args: argparse.Namespace, paths: AppPaths) -> int:
    if args.command == "setup":
        ui = select_ui(args.ui)
        await run_setup(ui, host_override=args.host, install_service=not args.no_service, paths=paths)
        return 0
    if args.command in {"on", "off", "status", "inputs", "input"}:
        return await _tv_command(args, paths)
    if args.command == "diagnose":
        report = await collect_diagnostics(paths)
        print(diagnostics_json(report) if args.json else render_diagnostics(report))
        return 0
    if args.command == "controller":
        if args.controller_action == "wol":
            print(wol_fallback_summary())
            return 0
        config = load_config(paths)
        if args.controller_action == "setup":
            run_controller_wake_setup(select_ui(args.ui), config, paths)
            return 0
        if args.controller_action == "test":
            ui = select_ui(args.ui)
            verified = test_controller_wake(ui, config)
            config.controller_wake.verified = verified
            save_config(config, paths)
            if verified:
                print("Controller wake verified.")
                return 0
            print(wol_fallback_summary())
            return 1
        if args.controller_action == "status":
            report = await collect_diagnostics(paths)
            print(render_controller_wake(report))
            saved = config.controller_wake
            print("\nSaved controller wake configuration")
            print("===================================")
            print(f"enabled: {saved.enabled}")
            print(f"controller: {saved.controller_name or 'none'}")
            print(f"mode: {saved.mode}")
            print(f"USB root: {saved.usb_root or 'none'}")
            print(f"PCI controller: {saved.pci_controller or 'none'}")
            print(f"settle delay: {saved.settle_delay_seconds:g} seconds")
            print(f"verified: {saved.verified}")
            print(f"reboot required: {controller_wake_reboot_required(config)}")
            print(f"udev rule: {saved.rule_path}")
            return 0 if saved.enabled else 1
        if args.controller_action == "disable":
            remove_wake_configuration()
            config.controller_wake = ControllerWakeConfig()
            save_config(config, paths)
            print("Removed the persistent controller wake rule. Current sysfs state may remain until reboot.")
            return 0
        raise ValueError(args.controller_action)
    if args.command == "test":
        return await _test_command(args, paths)
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
        config = load_config(paths)
        ADBController(config)  # Validate the saved adb path before enabling the watcher.
        path = install_user_service(paths)
        print(f"Installed and started user service: {path}")
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
        shutil.rmtree(paths.state_dir, ignore_errors=True)
        shutil.rmtree(paths.runtime_dir, ignore_errors=True)
    scheduled = args.remove_runtime and _schedule_runtime_removal(paths)
    print("Removed user-service integration.")
    if args.purge:
        print("Removed configuration and state. ADB authorization keys in ~/.android were left intact.")
    if scheduled:
        print("The managed runtime and launcher will be removed after this command exits.")
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
    except ADBNotInstalled:
        print(installation_help(), file=sys.stderr)
        return 2
    except (TVControlError, FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except UserCancelled:
        print("Cancelled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
