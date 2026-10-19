"""Inicia o assistente sozinho quando o usuário entra no sistema.

Windows: um .bat na pasta Inicializar do usuário; Linux: um .desktop em ~/.config/autostart;
macOS: um LaunchAgent (.plist). Só cria ou apaga esse arquivo do próprio assistente: não mexe
no jogo, na Steam nem em nada que precise de administrador.
"""

import os
import plistlib
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from .steam_paths import home_dir, platform_kind

DEFAULT_NAME = "DotaDraftAssistant"
WINDOWS_STARTUP = Path("Microsoft/Windows/Start Menu/Programs/Startup")

_BAT_SAFE = re.compile(r"[\w+=:,./\\-]+")
_EXEC_RESERVED = frozenset(" \t\n\"'\\><~|&;$*?#()`")


def _check_name(name: str) -> None:
    if not name or name.strip() != name or any(c in name for c in '/\\:*?"<>|'):
        raise ValueError(f"nome inválido para a entrada de inicialização: {name!r}")


def startup_entry_path(name: str = DEFAULT_NAME, platform: str | None = None,
                       env: Mapping | None = None) -> Path:
    """Onde fica o arquivo de inicialização automática nesta plataforma."""
    _check_name(name)
    env = os.environ if env is None else env
    kind = platform_kind(platform)
    home = home_dir(env, platform)
    if kind == "windows":
        appdata = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData/Roaming"
        return appdata / WINDOWS_STARTUP / f"{name}.bat"
    if kind == "macos":
        return home / "Library/LaunchAgents" / f"com.{name}.plist"
    config = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    return config / "autostart" / f"{name}.desktop"


def _bat_quote(arg: str) -> str:
    """Aspas no padrão do Windows (CRT) e `%` dobrado, que num .bat viraria variável."""
    if not _BAT_SAFE.fullmatch(arg):
        arg = re.sub(r'(\\*)"', r'\1\1\\"', arg)  # barras antes de aspas dobram e a aspa é escapada
        arg = '"' + re.sub(r"(\\+)$", r"\1\1", arg) + '"'  # barras finais não podem escapar a aspa
    return arg.replace("%", "%%")


def _exec_quote(arg: str) -> str:
    """Um argumento da chave Exec do .desktop (especificação freedesktop)."""
    if not arg or set(arg) & _EXEC_RESERVED:
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("%", "%%")


def _desktop_value(value: str) -> str:
    """Escapes de valor string do .desktop (aplicados por cima das aspas do Exec)."""
    return (value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


def _bat_text(command: list[str], workdir: Path) -> str:
    return (
        "@echo off\n"
        "chcp 65001 >nul\n"  # o arquivo é UTF-8: pastas com acento (ex.: C:\Users\João) funcionam
        "rem Dota Draft Assistant: inicia junto com o Windows. Apague este arquivo para desativar.\n"
        f'cd /d "{str(workdir).replace("%", "%%")}"\n'
        f'start "" {" ".join(_bat_quote(a) for a in command)}\n'
    )


def _desktop_text(command: list[str], workdir: Path, name: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_desktop_value(name)}\n"
        "Comment=Assistente de draft para Dota 2 (só lê a tela e o GSI oficial)\n"
        f"Exec={_desktop_value(' '.join(_exec_quote(a) for a in command))}\n"
        f"Path={_desktop_value(str(workdir))}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _plist_bytes(command: list[str], workdir: Path, name: str) -> bytes:
    return plistlib.dumps({
        "Label": f"com.{name}",
        "ProgramArguments": list(command),
        "WorkingDirectory": str(workdir),
        "RunAtLoad": True,
    })


def install(command: list[str], workdir: Path, name: str = DEFAULT_NAME, platform: str | None = None,
            env: Mapping | None = None) -> Path:
    """Grava a entrada de inicialização que roda `command` dentro de `workdir`; devolve o caminho dela."""
    if not command:
        raise ValueError("command vazio")
    command = [str(a) for a in command]
    workdir = Path(workdir).absolute()
    path = startup_entry_path(name, platform, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = platform_kind(platform)
    if kind == "windows":
        path.write_text(_bat_text(command, workdir), encoding="utf-8", newline="\r\n")
    elif kind == "macos":
        path.write_bytes(_plist_bytes(command, workdir, name))
    else:
        path.write_text(_desktop_text(command, workdir, name), encoding="utf-8")
    return path


def uninstall(name: str = DEFAULT_NAME, platform: str | None = None, env: Mapping | None = None) -> bool:
    """Apaga a entrada de inicialização; False se ela não existia."""
    try:
        startup_entry_path(name, platform, env).unlink()
    except FileNotFoundError:
        return False
    return True


def is_installed(name: str = DEFAULT_NAME, platform: str | None = None, env: Mapping | None = None) -> bool:
    return startup_entry_path(name, platform, env).is_file()


def default_command(python: str = sys.executable) -> list[str]:
    """Comando do modo automático; no Windows prefere o pythonw.exe (sem janela de console) ao lado do python.exe."""
    python = python or "python"
    exe = Path(python)
    name = exe.name.lower()
    if name.startswith("python") and name.endswith(".exe") and not name.startswith("pythonw"):
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            python = str(windowless)
    return [python, "-m", "draft_assistant", "auto"]
