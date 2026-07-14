"""Small terminal/KDialog/Zenity abstraction for the setup wizard."""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


class UserCancelled(RuntimeError):
    pass


class UI(ABC):
    name = "unknown"

    @abstractmethod
    def info(self, title: str, message: str) -> None: ...

    @abstractmethod
    def error(self, title: str, message: str) -> None: ...

    @abstractmethod
    def confirm(self, title: str, message: str, *, default: bool = True) -> bool: ...

    @abstractmethod
    def prompt(self, title: str, message: str, *, default: str = "") -> str: ...

    @abstractmethod
    def choose(self, title: str, message: str, choices: list[str]) -> int: ...


@dataclass
class TerminalUI(UI):
    name = "terminal"

    def info(self, title: str, message: str) -> None:
        print(f"\n== {title} ==\n{message}\n")

    def error(self, title: str, message: str) -> None:
        print(f"\n!! {title} !!\n{message}\n")

    def confirm(self, title: str, message: str, *, default: bool = True) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        answer = input(f"\n{title}: {message} {suffix} ").strip().casefold()
        if not answer:
            return default
        return answer in {"y", "yes"}

    def prompt(self, title: str, message: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        value = input(f"\n{title}: {message}{suffix}: ").strip()
        if not value and default:
            return default
        if not value:
            raise UserCancelled("No value entered")
        return value

    def choose(self, title: str, message: str, choices: list[str]) -> int:
        if not choices:
            raise ValueError("No choices supplied")
        print(f"\n== {title} ==\n{message}")
        for index, choice in enumerate(choices, start=1):
            print(f"  {index}. {choice}")
        while True:
            raw = input("Select an option: ").strip()
            try:
                selected = int(raw)
            except ValueError:
                print("Enter a number from the list.")
                continue
            if 1 <= selected <= len(choices):
                return selected - 1
            print("Enter a number from the list.")


class DialogUI(UI):
    command = ""

    def _run(self, args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.command, *args],
            check=False,
            text=True,
            capture_output=capture,
        )
        return result


class ZenityUI(DialogUI):
    name = "zenity"
    command = "zenity"

    def info(self, title: str, message: str) -> None:
        self._run(["--info", "--title", title, "--text", message, "--width", "520"])

    def error(self, title: str, message: str) -> None:
        self._run(["--error", "--title", title, "--text", message, "--width", "520"])

    def confirm(self, title: str, message: str, *, default: bool = True) -> bool:
        result = self._run(["--question", "--title", title, "--text", message, "--width", "520"])
        return result.returncode == 0

    def prompt(self, title: str, message: str, *, default: str = "") -> str:
        args = ["--entry", "--title", title, "--text", message, "--width", "520"]
        if default:
            args += ["--entry-text", default]
        result = self._run(args, capture=True)
        if result.returncode != 0:
            raise UserCancelled("Dialog cancelled")
        value = result.stdout.strip()
        if not value:
            raise UserCancelled("No value entered")
        return value

    def choose(self, title: str, message: str, choices: list[str]) -> int:
        args = [
            "--list",
            "--radiolist",
            "--title",
            title,
            "--text",
            message,
            "--column",
            "Select",
            "--column",
            "Option",
            "--hide-header",
            "--width",
            "620",
            "--height",
            "420",
        ]
        for index, choice in enumerate(choices):
            args += ["TRUE" if index == 0 else "FALSE", choice]
        result = self._run(args, capture=True)
        if result.returncode != 0:
            raise UserCancelled("Dialog cancelled")
        selected = result.stdout.strip()
        try:
            return choices.index(selected)
        except ValueError as exc:
            raise UserCancelled("No option selected") from exc


class KDialogUI(DialogUI):
    name = "kdialog"
    command = "kdialog"

    def info(self, title: str, message: str) -> None:
        self._run(["--title", title, "--msgbox", message])

    def error(self, title: str, message: str) -> None:
        self._run(["--title", title, "--error", message])

    def confirm(self, title: str, message: str, *, default: bool = True) -> bool:
        result = self._run(["--title", title, "--yesno", message])
        return result.returncode == 0

    def prompt(self, title: str, message: str, *, default: str = "") -> str:
        args = ["--title", title, "--inputbox", message]
        if default:
            args.append(default)
        result = self._run(args, capture=True)
        if result.returncode != 0:
            raise UserCancelled("Dialog cancelled")
        value = result.stdout.strip()
        if not value:
            raise UserCancelled("No value entered")
        return value

    def choose(self, title: str, message: str, choices: list[str]) -> int:
        args = ["--title", title, "--radiolist", message]
        for index, choice in enumerate(choices):
            args += [str(index), choice, "on" if index == 0 else "off"]
        result = self._run(args, capture=True)
        if result.returncode != 0:
            raise UserCancelled("Dialog cancelled")
        try:
            return int(result.stdout.strip())
        except ValueError as exc:
            raise UserCancelled("No option selected") from exc


def select_ui(preference: str = "auto") -> UI:
    preference = preference.casefold()
    if preference == "terminal":
        return TerminalUI()
    if preference == "kdialog":
        if not shutil.which("kdialog"):
            raise RuntimeError("kdialog was requested but is not installed")
        return KDialogUI()
    if preference == "zenity":
        if not shutil.which("zenity"):
            raise RuntimeError("zenity was requested but is not installed")
        return ZenityUI()
    if preference != "auto":
        raise ValueError("UI must be auto, terminal, kdialog, or zenity")

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()
    graphical = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if graphical and "kde" in desktop and shutil.which("kdialog"):
        return KDialogUI()
    if graphical and shutil.which("zenity"):
        return ZenityUI()
    if graphical and shutil.which("kdialog"):
        return KDialogUI()
    return TerminalUI()
