import configparser
import plistlib

import pytest

from draft_assistant.autostart import default_command, install, is_installed, startup_entry_path, uninstall

WIN_PYTHON = r"C:\Program Files\Python311\pythonw.exe"
COMMAND_TAIL = ["-m", "draft_assistant", "auto"]


def test_startup_entry_paths(tmp_path):
    roaming = tmp_path / "Roaming"
    assert startup_entry_path(platform="win32", env={"APPDATA": str(roaming)}) == (
        roaming / "Microsoft/Windows/Start Menu/Programs/Startup/DotaDraftAssistant.bat")
    assert startup_entry_path("Outro", "win32", {"USERPROFILE": str(tmp_path)}) == (
        tmp_path / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/Outro.bat")
    assert startup_entry_path(platform="linux", env={"HOME": str(tmp_path)}) == (
        tmp_path / ".config/autostart/DotaDraftAssistant.desktop")
    assert startup_entry_path(platform="linux", env={"HOME": "/x", "XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "autostart/DotaDraftAssistant.desktop")
    assert startup_entry_path(platform="darwin", env={"HOME": str(tmp_path)}) == (
        tmp_path / "Library/LaunchAgents/com.DotaDraftAssistant.plist")


@pytest.mark.parametrize("name", ["", "../fora", "a/b", "a\\b", " espaço"])
def test_invalid_names_rejected(name, tmp_path):
    with pytest.raises(ValueError):
        startup_entry_path(name, "linux", {"HOME": str(tmp_path)})


def test_install_windows_bat_quotes_spaces_and_percent(tmp_path):
    env = {"APPDATA": str(tmp_path / "Roaming")}
    workdir = tmp_path / "Meus Documentos" / "dota draft"
    command = [WIN_PYTHON, *COMMAND_TAIL, "--data-dir", "dados 100%", "C:\\pasta com fim\\"]
    path = install(command, workdir, platform="win32", env=env)
    assert path == startup_entry_path(platform="win32", env=env)
    raw = path.read_bytes()
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == "@echo off" and "chcp 65001 >nul" in lines
    assert f'cd /d "{workdir}"' in lines
    assert ('start "" "C:\\Program Files\\Python311\\pythonw.exe" -m draft_assistant auto '
            '--data-dir "dados 100%%" "C:\\pasta com fim\\\\"') in lines


def test_install_windows_bat_escapes_embedded_quotes(tmp_path):
    path = install(["prog.exe", 'diz "oi"', "a&b"], tmp_path, platform="win32", env={"APPDATA": str(tmp_path)})
    assert 'start "" prog.exe "diz \\"oi\\"" "a&b"' in path.read_text(encoding="utf-8").splitlines()


def read_desktop(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    return parser["Desktop Entry"]


def test_install_linux_desktop_quotes_spaces(tmp_path):
    env = {"HOME": str(tmp_path)}
    workdir = tmp_path / "pasta com espaço"
    path = install(["/opt/meu python/bin/python3", *COMMAND_TAIL], workdir, platform="linux", env=env)
    assert path == tmp_path / ".config/autostart/DotaDraftAssistant.desktop"
    entry = read_desktop(path)
    assert entry["Type"] == "Application"
    assert entry["Exec"] == '"/opt/meu python/bin/python3" -m draft_assistant auto'
    assert entry["Path"] == str(workdir)
    assert entry["Terminal"] == "false"


def test_install_linux_desktop_escapes_reserved_characters(tmp_path):
    path = install(["/usr/bin/python3", "$HOME", "50%", 'a"b', "c\\d"], tmp_path, platform="linux",
                   env={"HOME": str(tmp_path)})
    # \ vira \\ pelas aspas do Exec e de novo pelo escape de string do .desktop
    assert read_desktop(path)["Exec"] == '/usr/bin/python3 "\\\\$HOME" 50%% "a\\\\"b" "c\\\\\\\\d"'


def test_install_macos_plist(tmp_path):
    env = {"HOME": str(tmp_path)}
    workdir = tmp_path / "Dota Draft"
    command = ["/Library/Frameworks/Python 3.11/bin/python3", *COMMAND_TAIL]
    path = install(command, workdir, platform="darwin", env=env)
    assert path == tmp_path / "Library/LaunchAgents/com.DotaDraftAssistant.plist"
    data = plistlib.loads(path.read_bytes())
    assert data == {"Label": "com.DotaDraftAssistant", "ProgramArguments": command,
                    "WorkingDirectory": str(workdir), "RunAtLoad": True}


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_uninstall_and_is_installed(platform, tmp_path):
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "APPDATA": str(tmp_path / "Roaming")}
    assert not is_installed(platform=platform, env=env)
    assert uninstall(platform=platform, env=env) is False
    install(["python", *COMMAND_TAIL], tmp_path, platform=platform, env=env)
    assert is_installed(platform=platform, env=env)
    assert uninstall(platform=platform, env=env) is True
    assert not is_installed(platform=platform, env=env)
    assert uninstall(platform=platform, env=env) is False


def test_install_rejects_empty_command(tmp_path):
    with pytest.raises(ValueError):
        install([], tmp_path, platform="linux", env={"HOME": str(tmp_path)})


def test_install_reinstall_overwrites(tmp_path):
    env = {"HOME": str(tmp_path)}
    install(["velho"], tmp_path, platform="linux", env=env)
    path = install(["novo", *COMMAND_TAIL], tmp_path, platform="linux", env=env)
    assert read_desktop(path)["Exec"] == "novo -m draft_assistant auto"


def test_default_command_prefers_pythonw_next_to_python_exe(tmp_path):
    python = tmp_path / "Scripts" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    assert default_command(str(python)) == [str(python), *COMMAND_TAIL]
    (python.parent / "pythonw.exe").write_bytes(b"")
    assert default_command(str(python)) == [str(python.parent / "pythonw.exe"), *COMMAND_TAIL]
    assert default_command("/usr/bin/python3") == ["/usr/bin/python3", *COMMAND_TAIL]
    assert default_command()[1:] == COMMAND_TAIL
