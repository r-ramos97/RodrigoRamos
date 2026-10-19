import json
import os
import sys
import time
import types
import urllib.request

import pytest
import requests

from draft_assistant import auto, autostart, cli, gsi, screen, steam_paths, ui
from draft_assistant.cli import App, DataRefresher, LiveDraft, build_parser, main, refresh_reason
from draft_assistant.data_sources import JsonCache, OpenDotaClient
from draft_assistant.models import AutoUpdate, DraftState, TurnInfo
from draft_assistant.scoring import Engine
from fixtures import HERO_STATS, MATCHUPS

PATCHES = [{"name": "7.41d", "id": 59}, {"name": "7.41e", "id": 60}]
HERO_IDS = [r["id"] for r in HERO_STATS]
SELECTION = "DOTA_GAMERULES_STATE_HERO_SELECTION"
IN_GAME = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"


def seed(tmp_path, patch=None):
    cache = JsonCache(tmp_path / "cache")
    cache.put("opendota:/heroStats:{}", HERO_STATS)
    for hid, rows in MATCHUPS.items():
        cache.put(f"opendota:/heroes/{hid}/matchups:{{}}", rows)
    if patch:
        (tmp_path / "state.json").write_text(json.dumps({"patch": patch}), encoding="utf-8")


def base_args(tmp_path):
    return ["--config", str(tmp_path / "none.toml"), "--data-dir", str(tmp_path)]


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeOpenDota:
    """Sessão HTTP falsa do OpenDota: responde por caminho, conta as chamadas e simula queda da rede."""

    def __init__(self, patches=PATCHES, offline=False):
        self.patches, self.offline, self.calls, self.headers = list(patches), offline, [], {}

    def get(self, url, params=None, timeout=None):
        path = url.removeprefix(OpenDotaClient.base_url)
        self.calls.append(path)
        if self.offline:
            raise requests.ConnectionError("sem rede")
        if path == "/heroStats":
            return FakeResponse(HERO_STATS)
        if path == "/constants/patch":
            return FakeResponse(self.patches)
        return FakeResponse(MATCHUPS.get(int(path.split("/")[2]), []))


def make_app(tmp_path, session):
    app = App(build_parser().parse_args([*base_args(tmp_path), "suggest"]))
    app.opendota = OpenDotaClient(app.cache, session=session)
    app.opendota.min_interval = 0
    return app


@pytest.fixture
def fake_api(monkeypatch):
    session = FakeOpenDota()

    def factory(cache, api_key=None, **kwargs):
        client = OpenDotaClient(cache, api_key, session=session)
        client.min_interval = 0
        return client

    monkeypatch.setattr(cli, "OpenDotaClient", factory)
    return session


class FakeDisplay:
    def __init__(self, script=None):
        self.views, self.script, self.closed = [], script, False

    def push(self, view):
        self.views.append(view)

    def run(self, stop_event=None):
        if self.script:
            self.script(self)
        if stop_event:
            stop_event.set()

    def close(self):
        self.closed = True


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("tempo esgotado")
        time.sleep(0.01)


def gsi_payload(game_state=None, draft=None, token="segredo"):
    body = {"auth": {"token": token}, "player": {"team_name": "radiant"}}
    if game_state:
        body["map"] = {"game_state": game_state}
    if draft is not None:
        body["draft"] = draft
    return body


# --- comandos antigos ---------------------------------------------------------------------------

def test_suggest_offline(tmp_path, capsys):
    seed(tmp_path)
    main([*base_args(tmp_path), "suggest", "--enemies", "am", "--top", "3"])
    out = capsys.readouterr().out
    assert "Inimigos: Anti-Mage" in out
    assert out.split("Picks sugeridos:")[1].strip().startswith("1. Axe")


def test_unknown_hero_hint(tmp_path):
    seed(tmp_path)
    with pytest.raises(SystemExit, match="Crystal Maiden"):
        main([*base_args(tmp_path), "suggest", "--enemies", "cristal maiden"])


def test_no_subcommand_runs_auto(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_auto", lambda app, args: seen.append((args.command, args.no_gui)))
    assert main(base_args(tmp_path)) == 0
    assert seen == [("auto", False)]


# --- cache e atualização automática -------------------------------------------------------------

def test_cache_age_corrupt_file_and_atomic_put(tmp_path):
    cache = JsonCache(tmp_path)
    assert cache.age("x") is None
    cache.put("x", [1])
    assert 0 <= cache.age("x") < 5 and cache.get("x") == [1]
    assert [p.suffix for p in tmp_path.iterdir()] == [".json"]  # sem temporário sobrando
    cache._path("x").write_text("{meio arquivo", encoding="utf-8")
    assert cache.get("x") is None


def test_refresh_on_patch_change(tmp_path):
    seed(tmp_path, patch="7.41e")
    api = FakeOpenDota()
    app = make_app(tmp_path, api)
    assert refresh_reason(app) is None
    assert api.calls == ["/constants/patch"]  # cache em dia: só o patch usa a rede

    api.patches.append({"name": "7.42", "id": 61})
    api.calls.clear()
    engines, notes = [], []
    refresher = DataRefresher(app, engines.append, notes.append)
    assert refresher.check() is True
    # Patch novo: heroStats e todos os confrontos de novo, mesmo com o cache dentro da validade.
    assert api.calls.count("/heroStats") == 1
    assert sorted(c for c in api.calls if c.endswith("/matchups")) == [f"/heroes/{h}/matchups" for h in HERO_IDS]
    assert app.load_state()["patch"] == "7.42"
    assert len(engines) == 1 and isinstance(engines[0], Engine)
    assert notes == ["atualizando dados...", None]

    api.calls.clear()
    assert refresher.check() is False and api.calls == ["/constants/patch"]


def test_refresh_when_data_is_older_than_a_day(tmp_path):
    seed(tmp_path, patch="7.41e")
    old = time.time() - 25 * 3600
    os.utime(JsonCache(tmp_path / "cache")._path("opendota:/heroStats:{}"), (old, old))
    api = FakeOpenDota()
    app = make_app(tmp_path, api)
    assert refresh_reason(app) == "dados com 25 h" and api.calls == []
    assert DataRefresher(app, lambda engine: None).check() is True
    assert app.opendota.cache_age("/heroStats") < 5


def test_unknown_stored_patch_triggers_refresh(tmp_path):
    seed(tmp_path)  # cache de uma versão antiga, sem state.json
    assert refresh_reason(make_app(tmp_path, FakeOpenDota())) == "patch novo: ? -> 7.41e"


def test_refresh_without_network_keeps_working_from_cache(tmp_path, capsys):
    seed(tmp_path, patch="7.41e")
    old = time.time() - 30 * 3600
    os.utime(JsonCache(tmp_path / "cache")._path("opendota:/heroStats:{}"), (old, old))
    app = make_app(tmp_path, FakeOpenDota(offline=True))
    engines, notes = [], []
    assert DataRefresher(app, engines.append, notes.append).check() is False
    assert engines == [] and notes == ["atualizando dados...", "sem rede: usando o cache"]
    assert "seguindo com o cache" in capsys.readouterr().err
    assert app.load_state()["patch"] == "7.41e"
    with pytest.raises(requests.ConnectionError):  # atualização forçada não finge que deu certo
        app.opendota.hero_stats(refresh=True)
    assert app.opendota.hero_stats() == HERO_STATS  # leitura normal ainda cai no cache antigo
    assert app.engine().suggest_picks(DraftState(enemies=[1]), 1)[0].hero.localized_name == "Axe"


# --- modo automático ----------------------------------------------------------------------------

def test_live_draft_view_notes_and_final_draft(tmp_path):
    seed(tmp_path)
    app = make_app(tmp_path, FakeOpenDota())
    display = FakeDisplay()
    live = LiveDraft(display, app.engine(), top=2)
    live.note("screen", "tela desligada: falta calibrar")
    assert display.views == []  # nada para redesenhar ainda

    live.on_update(AutoUpdate("idle"))
    assert display.views[-1]["status"] == "Fora do draft · tela desligada: falta calibrar"
    assert display.views[-1]["picks"] == []

    draft = DraftState(allies=[3], enemies=[1])
    live.on_update(AutoUpdate("draft", "dire", draft, TurnInfo(True, False, 9), "screen"))
    view = display.views[-1]
    assert view["turn"] == "SUA VEZ: BAN (9s)" and view["focus"] == "bans" and view["source"] == "Tela"
    assert list(view).index("bans_suggested") < list(view).index("picks")
    assert len(view["picks"]) == 2 and view["picks"][0]["name"] == "Axe"
    assert {p["name"] for p in view["picks"] + view["bans_suggested"]}.isdisjoint({"Bane", "Anti-Mage"})

    live.note("data", "atualizando dados...")
    live.note("data", "atualizando dados...")  # igual: não redesenha
    assert len(display.views) == 3 and display.views[-1]["status"].endswith("atualizando dados...")
    live.set_engine(app.engine())
    live.note("data", None)
    assert len(display.views) == 5 and "atualizando" not in display.views[-1]["status"]

    live.on_update(AutoUpdate("done", "dire", draft, None, "screen"))
    assert display.views[-1]["status"].startswith("Draft encerrado") and display.views[-1]["picks"] == []
    live.on_update(AutoUpdate("idle"))  # logo depois do "done": o draft final continua na tela
    assert display.views[-1]["enemies"] == ["Anti-Mage"] and len(display.views) == 6
    live.on_update(AutoUpdate("draft", "dire", DraftState(), None, "gsi"))
    assert display.views[-1]["enemies"] == [] and display.views[-1]["picks"]


def test_auto_wiring_gsi_to_display(tmp_path, monkeypatch, capsys):
    seed(tmp_path, patch="7.41e")
    (tmp_path / "state.json").write_text(json.dumps({"patch": "7.41e", "gsi_token": "segredo"}), encoding="utf-8")
    servers, made = [], []

    class RecordingServer(gsi.GSIServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed = False
            servers.append(self)

        def shutdown(self):
            super().shutdown()
            self.closed = True

    def script(display):
        server = servers[0]
        port = server.httpd.server_address[1]
        request = urllib.request.Request(f"http://127.0.0.1:{port}/", json.dumps(gsi_payload()).encode(),
                                         {"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=5).close()  # menu, pela rede de verdade
        wait_for(lambda: len(display.views) == 1)
        server.handle_payload(gsi_payload(SELECTION, {"team2": {"pick0_id": 9}}, token="errado"))
        draft = {"activeteam": 2, "pick": True, "activeteam_time_remaining": 20,
                 "team2": {"pick0_id": 0}, "team3": {"pick0_id": 1}}
        server.handle_payload(gsi_payload(SELECTION, draft))
        server.handle_payload(gsi_payload(IN_GAME))
        server.handle_payload(gsi_payload(IN_GAME))

    display = FakeDisplay(script)

    def fake_make_display(prefer_gui=True, geometry_file=None):
        made.append((prefer_gui, geometry_file))
        return display

    monkeypatch.setattr(gsi, "GSIServer", RecordingServer)
    monkeypatch.setattr(ui, "make_display", fake_make_display)
    assert main([*base_args(tmp_path), "auto", "--no-gui", "--no-refresh", "--port", "0", "--top", "3"]) == 0

    assert made == [(False, tmp_path / "window.json")]
    assert servers[0].token == "segredo" and servers[0].closed  # token do gsi-config; servidor desligado
    idle, drafting, done = display.views
    assert idle["status"].startswith("Fora do draft") and "calibrate" in idle["status"]
    assert drafting["enemies"] == ["Anti-Mage"] and drafting["side"] == "Radiant" and drafting["source"] == "GSI"
    assert drafting["turn"] == "SUA VEZ: PICK (20s)" and drafting["my_turn"] is True
    assert [p["name"] for p in drafting["picks"]][0] == "Axe" and len(drafting["picks"]) == 3
    assert drafting["bans_suggested"]
    assert done["status"].startswith("Draft encerrado") and done["enemies"] == ["Anti-Mage"]
    captured = capsys.readouterr()
    assert "Aguardando o Dota 2" in captured.out and "Assistente encerrado" in captured.out
    assert "calibrate" in captured.err


def test_auto_first_run_downloads_data(tmp_path, monkeypatch, fake_api, capsys):
    monkeypatch.setattr(ui, "make_display", lambda prefer_gui=True, geometry_file=None: FakeDisplay())
    assert main([*base_args(tmp_path), "auto", "--no-gui", "--port", "0"]) == 0
    out = capsys.readouterr().out
    assert "Primeira execução" in out and f"confrontos {len(HERO_IDS)}/{len(HERO_IDS)}" in out
    assert "/heroStats" in fake_api.calls
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["patch"] == "7.41e"


def test_auto_without_data(tmp_path, monkeypatch, fake_api):
    monkeypatch.setattr(ui, "make_display", lambda prefer_gui=True, geometry_file=None: FakeDisplay())
    with pytest.raises(SystemExit, match="update-data"):
        main([*base_args(tmp_path), "auto", "--no-gui", "--no-refresh"])
    fake_api.offline = True
    with pytest.raises(SystemExit, match="internet"):
        main([*base_args(tmp_path), "auto", "--no-gui", "--port", "0"])


def test_screen_reader_from_calibration(tmp_path, monkeypatch):
    seed(tmp_path)
    app = make_app(tmp_path, FakeOpenDota())
    reader, problem = cli.screen_reader(app, None)
    assert reader is None and "calibrate" in problem

    screen.Calibration(my_side="radiant").save(tmp_path / "calibration.json")
    templates = {1: ["retrato"]}
    made, sides = [], []

    class FakeMatcher:
        @classmethod
        def from_directory(cls, directory, threshold=0.55):
            matcher = cls()
            matcher.templates, matcher.directory = templates, directory
            return matcher

    def fake_make_screen_reader(calibration, matcher):
        made.append((calibration.my_side, matcher.directory))
        return lambda side: sides.append(side) or DraftState()

    monkeypatch.setitem(sys.modules, "mss", types.ModuleType("mss"))
    monkeypatch.setattr(screen, "TemplateMatcher", FakeMatcher)
    monkeypatch.setattr(auto, "make_screen_reader", fake_make_screen_reader)

    reader, problem = cli.screen_reader(app, "dire")  # --side sobrepõe o lado da calibração
    assert problem is None and made == [("dire", tmp_path / "templates")]
    reader(None)
    reader("radiant")
    assert sides == ["dire", "radiant"]  # lado desconhecido => o da calibração; detectado => o detectado
    reader, _ = cli.screen_reader(app, None)
    reader(None)
    assert sides[-1] == "radiant"

    templates.clear()
    assert "update-data --templates" in cli.screen_reader(app, None)[1]
    monkeypatch.setitem(sys.modules, "mss", None)
    assert "pip install" in cli.screen_reader(app, None)[1]


# --- gsi-config, setup e início automático ------------------------------------------------------

def make_dota_dir(tmp_path):
    dota = tmp_path / "Steam" / "steamapps" / "common" / "dota 2 beta"
    (dota / "game" / "dota").mkdir(parents=True)
    return dota


def test_gsi_config_finds_dota_and_remembers_token(tmp_path, monkeypatch, capsys):
    dota = make_dota_dir(tmp_path)
    monkeypatch.setattr(steam_paths, "find_dota_dir", lambda roots=None: dota)
    main([*base_args(tmp_path), "gsi-config"])
    out = capsys.readouterr().out
    cfg = dota / gsi.CFG_SUBDIR / gsi.CFG_NAME
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert f"Dota 2 encontrado em {dota}" in out
    assert steam_paths.launch_option_instructions() in out
    assert state["gsi_port"] == 3000 and f'"{state["gsi_token"]}"' in cfg.read_text(encoding="utf-8")

    main([*base_args(tmp_path), "gsi-config", "--port", "3100"])  # rodar de novo mantém o token
    again = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert again["gsi_token"] == state["gsi_token"] and again["gsi_port"] == 3100
    assert "127.0.0.1:3100" in cfg.read_text(encoding="utf-8")


def test_gsi_config_without_dota(tmp_path, monkeypatch):
    monkeypatch.setattr(steam_paths, "find_dota_dir", lambda roots=None: None)
    with pytest.raises(SystemExit, match="(?s)Não encontrei a pasta do Dota 2.*--dota-dir"):
        main([*base_args(tmp_path), "gsi-config"])
    with pytest.raises(SystemExit, match="Pasta não encontrada"):
        main([*base_args(tmp_path), "gsi-config", "--dota-dir", str(tmp_path / "nada")])
    assert not (tmp_path / "state.json").exists()


def test_setup_chains_first_time_steps(tmp_path, monkeypatch, fake_api, capsys):
    dota = make_dota_dir(tmp_path)
    monkeypatch.setattr(steam_paths, "find_dota_dir", lambda roots=None: dota)
    monkeypatch.setattr(screen, "download_templates", lambda heroes, directory: len(heroes))
    assert main([*base_args(tmp_path), "setup"]) == 0
    out = capsys.readouterr().out
    for text in ("[1/2]", f"confrontos {len(HERO_IDS)}/{len(HERO_IDS)}", f"{len(HERO_IDS)} retratos baixados",
                 "[2/2]", "Config gravada", "draft_assistant calibrate", "draft_assistant auto", "autostart-on"):
        assert text in out
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["patch"] == "7.41e" and state["gsi_token"] and state["dota_dir"] == str(dota)

    monkeypatch.setattr(steam_paths, "find_dota_dir", lambda roots=None: None)
    fake_api.offline = True
    assert main([*base_args(tmp_path / "novo"), "setup"]) == 1  # sem rede e sem Dota: avisa e segue
    captured = capsys.readouterr()
    assert "rode setup de novo" in captured.err and "--dota-dir" in captured.err
    assert "Próximos passos" in captured.out


def test_autostart_on_and_off(tmp_path, monkeypatch, capsys):
    for key in ("APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "config"))
    for key in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(tmp_path))
    entry = autostart.startup_entry_path()
    data_dir = tmp_path / "dados"
    main(["--config", str(tmp_path / "none.toml"), "--data-dir", str(data_dir), "autostart-on"])
    assert entry.is_file() and str(entry) in capsys.readouterr().out
    text = entry.read_text(encoding="utf-8")
    assert "draft_assistant" in text and str(cli.PROJECT_DIR) in text
    assert str(data_dir) in text and text.index("--data-dir") < text.rindex("auto")

    main([*base_args(tmp_path), "autostart-off"])
    assert not entry.exists() and "desligado" in capsys.readouterr().out
    main([*base_args(tmp_path), "autostart-off"])
    assert "já estava desligado" in capsys.readouterr().out
