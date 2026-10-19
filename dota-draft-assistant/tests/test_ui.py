import json
import sys
import threading
import time
import types

from draft_assistant import ui
from draft_assistant.models import AutoUpdate, DraftState, TurnInfo
from draft_assistant.scoring import Suggestion
from fixtures import heroes

HEROES = heroes()
PICKS = [Suggestion(HEROES[2], 18.4, {"counter": 16.2, "synergy": 0.0, "meta": 2.0, "pro": 5.0, "coach": 0.0},
                    ["+16.2pp vs Anti-Mage", "prioridade nos drafts de coach", "terceira razão"]),
         Suggestion(HEROES[5], 1.5, {"counter": -1.0, "synergy": 0.0, "meta": 0.0, "pro": 0.1, "coach": 2.5})]
BANS = [Suggestion(HEROES[4], 3.0, {"counter": 3.1, "synergy": 0.0, "meta": 1.0, "pro": 0.0, "coach": 2.5}, [])]


def update(phase="draft", turn=None, state=None):
    return AutoUpdate(phase, "radiant", state or DraftState(allies=[3], enemies=[1], bans=[999]), turn, "gsi")


def test_build_view_idle():
    view = ui.build_view(AutoUpdate("idle"), HEROES, [], [])
    assert view["status"] == "Fora do draft"
    assert view["turn"] == "" and view["side"] == "" and view["source"] == ""
    assert view["allies"] == view["enemies"] == view["bans"] == []
    assert view["picks"] == view["bans_suggested"] == [] and view["focus"] == "picks"


def test_build_view_draft_my_pick():
    view = ui.build_view(update(turn=TurnInfo(True, True, 23.4)), HEROES, PICKS, BANS)
    assert view["status"] == "Draft em andamento"
    assert view["turn"] == "SUA VEZ: PICK (23s)" and view["my_turn"] is True
    assert view["side"] == "Radiant" and view["source"] == "GSI"
    assert view["focus"] == "picks"
    keys = list(view)
    assert keys.index("picks") < keys.index("bans_suggested")
    first = view["picks"][0]
    assert first["name"] == "Axe" and isinstance(first["score"], float)
    assert first["detail"] == "counter +16.2 | meta +2.0 | pro 5.0"
    assert first["reason"] == "+16.2pp vs Anti-Mage; prioridade nos drafts de coach"
    assert view["picks"][1]["detail"] == "counter -1.0 | pro 0.1 | coach 2.5"
    assert view["bans_suggested"][0] == {"name": "Bloodseeker", "score": 3.0,
                                         "detail": "counter +3.1 | meta +1.0 | coach 2.5", "reason": ""}


def test_build_view_my_ban_puts_bans_first():
    view = ui.build_view(update(turn=TurnInfo(True, False)), HEROES, PICKS, BANS)
    assert view["turn"] == "SUA VEZ: BAN"
    assert view["focus"] == "bans"
    keys = list(view)
    assert keys.index("bans_suggested") < keys.index("picks")


def test_turn_unknown_and_opponent():
    assert ui.build_view(update(turn=None), HEROES, [], [])["turn"] == ""
    assert ui.build_view(update(turn=TurnInfo()), HEROES, [], [])["turn"] == ""
    view = ui.build_view(update(turn=TurnInfo(False, False, 10)), HEROES, PICKS, BANS)
    assert view["turn"] == "Vez do adversário"
    assert view["my_turn"] is False and view["focus"] == "picks"


def test_build_view_done_ignores_turn():
    view = ui.build_view(update("done", TurnInfo(True, True, 5)), HEROES, [], [])
    assert view["status"] == "Draft encerrado"
    assert view["turn"] == "" and view["my_turn"] is False


def test_names_skip_unknown_ids():
    view = ui.build_view(update(state=DraftState(allies=[1, 999], enemies=[2, 5], bans=[0, 4])), HEROES, [], [])
    assert view["allies"] == ["Anti-Mage"]
    assert view["enemies"] == ["Axe", "Crystal Maiden"]
    assert view["bans"] == ["Bloodseeker"]


def test_terminal_view_prints_heroes_and_turn(capsys):
    view = ui.build_view(update(turn=TurnInfo(True, True, 23)), HEROES, PICKS, BANS)
    ui.TerminalView().push(view)
    out = capsys.readouterr().out
    assert out.startswith(ui.CLEAR)
    assert "SUA VEZ: PICK (23s)" in out
    assert "Aliados:  Bane" in out and "Inimigos: Anti-Mage" in out
    assert "Axe" in out and "Bloodseeker" in out
    assert out.index("Picks sugeridos") < out.index("Bans sugeridos")


def test_terminal_view_ban_focus_and_idle(capsys):
    term = ui.TerminalView()
    term.push(ui.build_view(update(turn=TurnInfo(True, False)), HEROES, PICKS, BANS))
    out = capsys.readouterr().out
    assert out.index("Bans sugeridos") < out.index("Picks sugeridos")
    term.push(ui.build_view(AutoUpdate("idle"), HEROES, [], []))
    assert ui.WAITING in capsys.readouterr().out


def test_terminal_run_returns_on_close_and_sets_stop():
    term, stop = ui.TerminalView(), threading.Event()
    threading.Timer(0.05, term.close).start()
    term.run(stop)
    assert stop.is_set()


def test_overlay_segments_follow_focus():
    view = ui.build_view(update(turn=TurnInfo(True, False, 9)), HEROES, PICKS, BANS)
    segments = ui.overlay_segments(view)
    texts = [text for text, _ in segments]
    assert ("SUA VEZ: BAN (9s)\n", "turn_me") in segments
    assert texts.index("BANS SUGERIDOS\n") < texts.index("PICKS SUGERIDOS\n")
    assert all(tag in ui.TAGS for _, tag in segments)


def test_make_display_falls_back_without_tkinter(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)  # faz "import tkinter" falhar
    display = ui.make_display()
    assert isinstance(display, ui.TerminalView)
    assert "tkinter" in capsys.readouterr().err


def test_make_display_terminal_when_gui_not_preferred(capsys):
    assert isinstance(ui.make_display(prefer_gui=False), ui.TerminalView)
    assert capsys.readouterr().err == ""


# --- Tk falso: testa a lógica da janela (fila, threads, geometria) sem tkinter instalado ---

class _Widget:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):  # pack, bind, configure... não interessam ao teste
        return lambda *args, **kwargs: None


class _FakeTk(_Widget):
    def __init__(self):
        self.attrs, self.pending, self.alive, self.geo, self.titles = {}, [], True, "300x400+5+5", []

    def attributes(self, name, value):
        self.attrs[name] = value

    def geometry(self, value=None):
        if value:
            self.geo = value
        return self.geo

    def title(self, text):
        self.titles.append(text)

    def after(self, ms, func):
        self.pending.append(func)

    def mainloop(self):
        for _ in range(1000):
            if not (self.alive and self.pending):
                return
            self.pending.pop(0)()
            time.sleep(0.005)

    def destroy(self):
        self.alive = False


class _FakeText(_Widget):
    def __init__(self, *args, **kwargs):
        self.chunks = []

    def insert(self, index, text, *tags):
        self.chunks.append(text)

    def delete(self, *args):
        self.chunks.clear()


class _TclError(Exception):
    pass


def fake_tkinter(tk_class=_FakeTk):
    module = types.ModuleType("tkinter")
    module.Tk, module.Text, module.TclError = tk_class, _FakeText, _TclError
    return module


def test_make_display_falls_back_without_display(monkeypatch, capsys):
    def no_display():
        raise _TclError("no display name and no $DISPLAY environment variable")

    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter(no_display))
    assert isinstance(ui.make_display(), ui.TerminalView)
    assert "$DISPLAY" in capsys.readouterr().err


def test_overlay_push_and_close_from_other_thread(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter())
    geometry_file = tmp_path / "ui" / "geometry.json"
    window = ui.make_display(geometry_file=geometry_file)
    assert isinstance(window, ui.OverlayWindow)
    assert window.root.attrs == {"-topmost": True, "-alpha": 0.92}
    view = ui.build_view(update(turn=TurnInfo(True, True, 20)), HEROES, PICKS, BANS)

    def worker():
        window.push(ui.build_view(AutoUpdate("idle"), HEROES, [], []))
        window.push(view)
        window.root.geo = "333x444+10+20"  # o jogador moveu a janela
        time.sleep(0.3)
        window.close()

    stop = threading.Event()
    threading.Thread(target=worker).start()
    window.run(stop)
    assert stop.is_set() and not window.root.alive
    shown = "".join(window.text.chunks)
    assert "SUA VEZ: PICK (20s)" in shown and "Axe" in shown and "Bloodseeker" in shown
    assert window.root.titles[-1] == "Dota Draft Assistant - SUA VEZ: PICK (20s)"
    assert json.loads(geometry_file.read_text(encoding="utf-8")) == {"geometry": "333x444+10+20"}


def test_overlay_restores_geometry_and_stops_with_event(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter())
    geometry_file = tmp_path / "geometry.json"
    geometry_file.write_text(json.dumps({"geometry": "320x500+-1200+50"}), encoding="utf-8")
    window = ui.OverlayWindow(alpha=0.8, geometry_file=geometry_file)
    assert window.root.geo == "320x500+-1200+50" and window.root.attrs["-alpha"] == 0.8
    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()
    window.run(stop)
    assert not window.root.alive


def test_overlay_ignores_bad_geometry_and_close_before_run(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter())
    geometry_file = tmp_path / "geometry.json"
    geometry_file.write_text("[1, 2]", encoding="utf-8")
    window = ui.OverlayWindow(geometry_file=geometry_file, default_geometry="200x300+1+1")
    assert window.root.geo == "200x300+1+1"
    window.close()  # mesma thread: fecha na hora
    stop = threading.Event()
    window.run(stop)
    assert stop.is_set() and not window.root.alive
