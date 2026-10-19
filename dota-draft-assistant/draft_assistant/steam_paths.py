"""Acha a instalação do Dota 2 sozinho, para o usuário nunca precisar digitar caminho.

Só LÊ: o caminho da Steam no registro do Windows e o `libraryfolders.vdf` de cada instalação
da Steam. Nunca altera arquivos de configuração da Steam; a opção `-gamestateintegration` é o
próprio usuário que coloca (veja `launch_option_instructions`).
"""

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

DOTA_SUBDIR = Path("steamapps/common/dota 2 beta")
VDF_LOCATIONS = (Path("steamapps/libraryfolders.vdf"), Path("config/libraryfolders.vdf"))
WINDOWS_STEAM = Path("C:/Program Files (x86)/Steam")
LAUNCH_OPTION = "-gamestateintegration"


def platform_kind(platform: str | None = None) -> str:
    """'windows', 'macos' ou 'linux' a partir de um valor no estilo `sys.platform`."""
    platform = (platform or sys.platform).lower()
    if platform.startswith("win"):
        return "windows"
    if platform.startswith(("darwin", "mac")):
        return "macos"
    return "linux"


def home_dir(env: Mapping | None = None, platform: str | None = None) -> Path:
    """Pasta do usuário: USERPROFILE/HOME no Windows, HOME nos demais; senão `Path.home()`."""
    env = os.environ if env is None else env
    keys = ("USERPROFILE", "HOME") if platform_kind(platform) == "windows" else ("HOME",)
    for key in keys:
        if env.get(key):
            return Path(env[key])
    return Path.home()


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:  # unidade desconectada, sem permissão...
        return False


def _unique(paths: Iterable[Path]) -> list[Path]:
    """Remove repetidos (inclusive via symlink e, no Windows, maiúsculas/minúsculas) mantendo a ordem."""
    seen, out = set(), []
    for path in paths:
        try:
            key = os.path.normcase(str(path.resolve()))
        except (OSError, RuntimeError):
            key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _registry_steam_path() -> str | None:
    """Valor `SteamPath` de HKEY_CURRENT_USER\\Software\\Valve\\Steam (None fora do Windows ou sem Steam)."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        return None
    return str(value) if value else None


def steam_roots(platform: str | None = None, env: Mapping | None = None) -> list[Path]:
    """Pastas de instalação da Steam que existem nesta máquina, sem repetição e na ordem de preferência."""
    env = os.environ if env is None else env
    kind = platform_kind(platform)
    home = home_dir(env, platform)
    if kind == "windows":
        registry, program_files = _registry_steam_path(), env.get("ProgramFiles(x86)")
        candidates = [Path(registry)] if registry else []
        if program_files:
            candidates.append(Path(program_files) / "Steam")
        candidates.append(WINDOWS_STEAM)
    elif kind == "macos":
        candidates = [home / "Library/Application Support/Steam"]
    else:
        candidates = [home / ".steam/steam", home / ".local/share/Steam",
                      home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    return _unique(p for p in candidates if _is_dir(p))


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Tokens do formato KeyValues da Valve: ("s", texto), ("{", "{") ou ("}", "}").

    Aceita strings com ou sem aspas, ignora comentários `//` e condicionais como `[$WIN32]`.
    Dentro de aspas só `\\\\` e `\\"` são escapes (a Steam grava `D:\\\\SteamLibrary`).
    """
    tokens, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif ch in "{}":
            tokens.append((ch, ch))
            i += 1
        elif ch == '"':
            buf, i = [], i + 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '\\"':
                    i += 1
                buf.append(text[i])
                i += 1
            tokens.append(("s", "".join(buf)))
            i += 1  # aspas de fechamento (ou fim do texto)
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            word = text[i:j]
            if not (word.startswith("[") and word.endswith("]")):
                tokens.append(("s", word))
            i = j
    return tokens


def _parse(tokens: list[tuple[str, str]], i: int = 0) -> tuple[dict, int]:
    """Monta o dicionário (chaves em minúsculas) a partir de `i`; tolera chaves sem valor e chaves soltas."""
    out: dict = {}
    while i < len(tokens):
        kind, key = tokens[i]
        if kind == "}":
            return out, i + 1
        if kind == "{" or i + 1 >= len(tokens):  # bloco sem nome ou chave sem valor no fim
            i += 1
            continue
        next_kind, value = tokens[i + 1]
        if next_kind == "{":
            out[key.lower()], i = _parse(tokens, i + 2)
        elif next_kind == "}":
            i += 1  # chave sem valor; o "}" fecha o bloco na próxima volta
        else:
            out[key.lower()] = value
            i += 2
    return out, i


def parse_libraryfolders(text: str) -> list[Path]:
    """Bibliotecas listadas no `steamapps/libraryfolders.vdf`, na ordem do arquivo.

    Formato novo: "libraryfolders" { "0" { "path" "D:\\\\SteamLibrary" ... } }.
    Formato antigo: "LibraryFolders" { "1" "D:\\\\SteamLibrary" } (a pasta da Steam fica implícita).
    """
    data, _ = _parse(_tokenize(text))
    folders = data.get("libraryfolders")
    if not isinstance(folders, dict):
        folders = next((v for v in data.values() if isinstance(v, dict)), data)
    out = []
    for key, value in folders.items():
        if not key.isdigit():
            continue
        raw = value.get("path") if isinstance(value, dict) else value
        if isinstance(raw, str) and raw.strip():
            out.append(Path(raw.strip()))
    return out


def library_dirs(roots: Iterable[Path]) -> list[Path]:
    """Cada raiz da Steam seguida das bibliotecas do seu libraryfolders.vdf (arquivos ausentes são ignorados)."""
    found = []
    for root in roots:
        root = Path(root)
        found.append(root)
        for rel in VDF_LOCATIONS:
            try:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found.extend(parse_libraryfolders(text))
    return _unique(found)


def find_dota_dir(roots: list[Path] | None = None) -> Path | None:
    """Primeira `<biblioteca>/steamapps/common/dota 2 beta` que tem `game/dota`; None se não achar."""
    for lib in library_dirs(steam_roots() if roots is None else roots):
        candidate = lib / DOTA_SUBDIR
        if _is_dir(candidate / "game" / "dota"):
            return candidate
    return None


def launch_option_instructions() -> str:
    """Passo a passo (pt-BR) para o usuário ativar o GSI na Steam; o assistente não mexe na Steam."""
    return (
        f"Para o Dota 2 enviar o draft ao assistente, adicione a opção {LAUNCH_OPTION} (só uma vez):\n"
        "  1. Abra a Steam e vá em Biblioteca.\n"
        "  2. Clique com o botão direito em Dota 2 e escolha Propriedades.\n"
        f"  3. Em Geral > Opções de inicialização, digite: {LAUNCH_OPTION}\n"
        "     (se já houver outras opções, deixe um espaço entre elas).\n"
        "  4. Feche a janela e abra (ou reinicie) o Dota 2.\n"
        "O assistente não altera a configuração da Steam sozinho; ele só lê o que o jogo envia."
    )
