import sys
from pathlib import Path

import pytest

from draft_assistant import steam_paths
from draft_assistant.steam_paths import (DOTA_SUBDIR, LAUNCH_OPTION, find_dota_dir, home_dir,
                                         launch_option_instructions, library_dirs, parse_libraryfolders,
                                         platform_kind, steam_roots)

NEW_FORMAT = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
		"contentid"		"123456789"
		"totalsize"		"0"
		"apps"
		{
			"228980"		"1234"
			"570"		"40000000000"
		}
	}
	"1"
	{
		"path"		"D:\\SteamLibrary"
		"label"		"Jogos \"rápidos\""
		"apps"
		{
		}
	}
}
'''

OLD_FORMAT = r'''
"LibraryFolders"
{
	"TimeNextStatsReport"		"1600000000"
	"ContentStatsID"		"-1234"
	"1"		"D:\\Games\\Steam"
	"2"		"E:\\SteamLibrary\\"
}
'''


def strs(paths):
    return [str(p) for p in paths]


def test_parse_new_format_unescapes_backslashes():
    assert strs(parse_libraryfolders(NEW_FORMAT)) == [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"]


def test_parse_old_flat_format_skips_non_numeric_keys():
    assert strs(parse_libraryfolders(OLD_FORMAT)) == [r"D:\Games\Steam", "E:\\SteamLibrary\\"]


def test_parse_tolerates_comments_conditionals_and_broken_files():
    text = '''// gerado pela Steam
    libraryfolders {
        "0" { path /home/joao/.local/share/Steam }  // sem aspas
        "1" { "path" "/mnt/jogos/Steam Library" [$LINUX] }
        "2" { "label" "sem path" }
        "3"
    '''
    assert strs(parse_libraryfolders(text)) == ["/home/joao/.local/share/Steam", "/mnt/jogos/Steam Library"]
    assert parse_libraryfolders("") == []
    assert parse_libraryfolders('"libraryfolders" { "0" { "path" "/x"') == [Path("/x")]
    assert parse_libraryfolders("}}} {{ \"lixo") == []


def test_platform_kind_and_home(tmp_path):
    assert [platform_kind(p) for p in ("win32", "linux", "darwin")] == ["windows", "linux", "macos"]
    assert home_dir({"USERPROFILE": str(tmp_path), "HOME": "/outro"}, "win32") == tmp_path
    assert home_dir({"HOME": str(tmp_path), "USERPROFILE": "/outro"}, "linux") == tmp_path
    assert home_dir({}, "linux") == Path.home()


def test_steam_roots_linux_existing_only_and_dedup_symlink(tmp_path):
    real = tmp_path / ".local/share/Steam"
    real.mkdir(parents=True)
    (tmp_path / ".steam").mkdir()
    (tmp_path / ".steam/steam").symlink_to(real, target_is_directory=True)
    flatpak = tmp_path / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
    flatpak.mkdir(parents=True)
    assert steam_roots("linux", {"HOME": str(tmp_path)}) == [tmp_path / ".steam/steam", flatpak]
    assert steam_roots("linux", {"HOME": str(tmp_path / "vazio")}) == []


def test_steam_roots_macos(tmp_path):
    steam = tmp_path / "Library/Application Support/Steam"
    steam.mkdir(parents=True)
    assert steam_roots("darwin", {"HOME": str(tmp_path)}) == [steam]


def test_steam_roots_windows_registry_then_program_files(tmp_path, monkeypatch):
    registry = tmp_path / "Steam do registro"
    program_files = tmp_path / "Program Files (x86)"
    registry.mkdir()
    (program_files / "Steam").mkdir(parents=True)
    monkeypatch.setattr(steam_paths, "_registry_steam_path", lambda: str(registry))
    env = {"USERPROFILE": str(tmp_path), "ProgramFiles(x86)": str(program_files)}
    assert steam_roots("win32", env) == [registry, program_files / "Steam"]
    monkeypatch.setattr(steam_paths, "_registry_steam_path", lambda: str(program_files / "Steam"))
    assert steam_roots("win32", env) == [program_files / "Steam"]  # mesmo caminho só uma vez
    monkeypatch.setattr(steam_paths, "_registry_steam_path", lambda: None)
    assert steam_roots("win32", {"USERPROFILE": str(tmp_path)}) == []


@pytest.mark.skipif(sys.platform == "win32", reason="sem winreg só fora do Windows")
def test_registry_lookup_without_winreg_returns_none():
    assert steam_paths._registry_steam_path() is None


def make_dota(lib: Path) -> Path:
    dota = lib / DOTA_SUBDIR
    (dota / "game/dota").mkdir(parents=True)
    return dota


def write_vdf(root: Path, *libs: Path) -> None:
    body = "".join(f'\t"{i}"\n\t{{\n\t\t"path"\t\t"{lib}"\n\t}}\n' for i, lib in enumerate(libs))
    (root / "steamapps").mkdir(parents=True, exist_ok=True)
    (root / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders"\n{{\n{body}}}\n', encoding="utf-8")


def test_find_dota_dir_in_secondary_library(tmp_path):
    root = tmp_path / "Steam"
    library = tmp_path / "Minha Biblioteca Steam"
    (root / DOTA_SUBDIR).mkdir(parents=True)  # pasta incompleta (sem game/dota): ignorada
    dota = make_dota(library)
    write_vdf(root, root, tmp_path / "não existe", library)
    assert library_dirs([root]) == [root, tmp_path / "não existe", library]
    assert find_dota_dir([root]) == dota


def test_find_dota_dir_in_root_without_vdf_and_order_of_roots(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    dota_second = make_dota(second)
    first.mkdir()
    assert find_dota_dir([first, second]) == dota_second
    dota_first = make_dota(first)
    assert find_dota_dir([first, second]) == dota_first


def test_find_dota_dir_old_vdf_location_and_missing(tmp_path):
    root, library = tmp_path / "Steam", tmp_path / "lib"
    dota = make_dota(library)
    (root / "config").mkdir(parents=True)
    (root / "config/libraryfolders.vdf").write_text(f'"LibraryFolders" {{ "1" "{library}" }}', encoding="utf-8")
    assert find_dota_dir([root]) == dota
    assert find_dota_dir([tmp_path / "nada"]) is None
    assert find_dota_dir([]) is None


def test_find_dota_dir_default_uses_steam_roots(tmp_path, monkeypatch):
    dota = make_dota(tmp_path)
    monkeypatch.setattr(steam_paths, "steam_roots", lambda: [tmp_path])
    assert find_dota_dir() == dota


def test_launch_option_instructions_ptbr():
    text = launch_option_instructions()
    assert LAUNCH_OPTION == "-gamestateintegration" and LAUNCH_OPTION in text
    for step in ("Steam", "Biblioteca", "Dota 2", "Propriedades", "Opções de inicialização"):
        assert step in text
