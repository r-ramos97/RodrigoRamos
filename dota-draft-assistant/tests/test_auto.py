import json
import threading
import urllib.request

import pytest

from draft_assistant.auto import AutoPilot, make_screen_reader
from draft_assistant.gsi import GSIServer, parse_turn
from draft_assistant.models import DraftState

SELECTION = "DOTA_GAMERULES_STATE_HERO_SELECTION"
STRATEGY = "DOTA_GAMERULES_STATE_STRATEGY_TIME"
IN_GAME = "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeScreen:
    """Devolve as leituras programadas, uma por chamada (repete a última); pode lançar exceções."""

    def __init__(self, *readings):
        self.readings = list(readings)
        self.sides = []

    def __call__(self, side):
        self.sides.append(side)
        item = self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]
        if isinstance(item, Exception):
            raise item
        return item


def payload(game_state=None, team="radiant", draft=None):
    body = {"provider": {"name": "Dota 2"}, "player": {"team_name": team} if team else {}}
    if game_state:
        body["map"] = {"game_state": game_state}
    if draft is not None:
        body["draft"] = draft
    return body


def gsi_draft(radiant_picks=(), dire_picks=(), bans=(), active=2, pick=True, seconds=25):
    team2 = {f"pick{i}_id": h for i, h in enumerate(radiant_picks)}
    team3 = {f"pick{i}_id": h for i, h in enumerate(dire_picks)}
    team2.update({f"ban{i}_id": h for i, h in enumerate(bans)})
    return {"activeteam": active, "pick": pick, "activeteam_time_remaining": seconds,
            "team2": team2 or {"pick0_id": 0}, "team3": team3 or {"pick0_id": 0}}


def pilot(**kwargs):
    events, clock = [], FakeClock()
    return AutoPilot(events.append, clock=clock, **kwargs), events, clock


def test_parse_turn():
    turn = parse_turn(payload(SELECTION, "dire", gsi_draft(active=3, pick=False, seconds=12.5)), None)
    assert (turn.my_turn, turn.is_pick, turn.seconds_left) == (True, False, 12.5)
    assert parse_turn(payload(SELECTION, "dire", gsi_draft(active=2)), "dire").my_turn is False
    assert parse_turn(payload(SELECTION, None, gsi_draft(active=2)), "radiant").my_turn is True
    assert parse_turn(payload(SELECTION, None, gsi_draft(active=0)), "radiant").my_turn is None
    assert parse_turn(payload(SELECTION, "radiant", {}), None) is None
    assert parse_turn(payload(SELECTION, "radiant"), None) is None


def test_gsi_only_draft_flow():
    auto, events, _ = pilot()
    auto.handle_gsi(payload(team=None))  # menu: sem "map"
    auto.handle_gsi(payload(team=None))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft()))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1], seconds=10)))  # só o relógio
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1], dire_picks=[2], bans=[3])))
    auto.handle_gsi(payload(STRATEGY, draft=gsi_draft(radiant_picks=[1], dire_picks=[2], bans=[3])))
    auto.handle_gsi(payload(IN_GAME))
    auto.handle_gsi(payload(IN_GAME))
    auto.handle_gsi(payload(IN_GAME))
    assert [e.phase for e in events] == ["idle", "draft", "draft", "draft", "done", "idle"]
    assert events[1].state.key() == ((), (), ()) and events[1].source == "gsi"
    assert events[2].state.allies == [1]
    assert events[3].state.key() == ((1,), (2,), (3,)) and events[3].side == "radiant"
    assert events[4].state.key() == ((1,), (2,), (3,)) and events[4].turn is None
    assert events[5].state is None
    assert auto.phase == "idle"


def test_new_draft_resets_previous_one():
    auto, events, _ = pilot()
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    auto.handle_gsi(payload(team=None))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft()))
    assert [e.phase for e in events] == ["draft", "done", "draft"]
    assert events[-1].state.key() == ((), (), ())


def test_side_detection_and_forced_side():
    auto, events, _ = pilot()
    auto.handle_gsi(payload(SELECTION, "dire", gsi_draft(radiant_picks=[1], dire_picks=[5])))
    assert auto.side == "dire"
    assert events[-1].state.allies == [5] and events[-1].state.enemies == [1]
    auto.handle_gsi(payload(SELECTION, None, gsi_draft(radiant_picks=[1], dire_picks=[5])))
    assert auto.side == "dire"  # lembrado

    forced, events, _ = pilot(forced_side="Radiant")
    forced.handle_gsi(payload(SELECTION, "dire", gsi_draft(radiant_picks=[1], dire_picks=[5], active=2)))
    assert forced.side == "radiant"
    assert events[-1].state.allies == [1] and events[-1].turn.my_turn is True
    with pytest.raises(ValueError):
        AutoPilot(print, forced_side="spectator")


def test_turn_changes_are_emitted_but_not_clock_ticks():
    auto, events, _ = pilot()
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(active=2, pick=False, seconds=30)))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(active=2, pick=False, seconds=29)))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(active=3, pick=False, seconds=30)))
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(active=3, pick=True, seconds=30)))
    turns = [(e.turn.my_turn, e.turn.is_pick) for e in events]
    assert turns == [(True, False), (False, False), (False, True)]


def test_screen_fallback_with_debounce_when_gsi_draft_is_empty():
    s1, s2 = DraftState(allies=[1]), DraftState(allies=[1], enemies=[2])
    screen = FakeScreen(DraftState(), s1, s1, s1, s2, s1, s1, s2, s2)
    auto, events, clock = pilot(screen_reader=screen)
    auto.tick()  # fora do draft: não lê a tela
    assert screen.sides == []
    auto.handle_gsi(payload(SELECTION, "dire", draft={}))  # matchmaking: bloco draft vazio
    for _ in range(9):
        clock.t += 1
        auto.tick()
        auto.handle_gsi(payload(SELECTION, "dire", draft={}))  # GSI segue chegando sem draft
    assert screen.sides == ["dire"] * 9
    assert [(e.phase, e.source, e.state.key()) for e in events] == [
        ("draft", "gsi", ((), (), ())),
        ("draft", "screen", s1.key()),
        ("draft", "screen", s2.key()),
    ]
    auto.handle_gsi(payload(IN_GAME))
    assert events[-1].phase == "done" and events[-1].state.key() == s2.key()


def test_gsi_draft_has_priority_over_screen():
    screen = FakeScreen(DraftState(allies=[9]))
    auto, events, clock = pilot(screen_reader=screen)
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    for _ in range(3):
        clock.t += 1
        auto.tick()
    assert screen.sides == []
    clock.t += auto.gsi_stale_seconds + 1  # GSI parou de trazer o draft: a tela assume
    auto.tick()
    auto.tick()
    assert len(screen.sides) == 2 and events[-1].source == "screen" and events[-1].state.allies == [9]


def test_screen_only_after_grace_period():
    s1 = DraftState(enemies=[4])
    screen = FakeScreen(DraftState(), DraftState(), s1, s1, s1, DraftState(), DraftState())
    auto, events, clock = pilot(screen_reader=screen, gsi_grace_seconds=20)
    clock.t = 10
    auto.tick()
    assert screen.sides == [] and auto.phase == "idle"
    clock.t = 21
    for _ in range(7):
        auto.tick()
        clock.t += 1
    assert auto.screen_only and auto.phase == "draft"
    assert screen.sides == ["radiant"] * 7
    assert [(e.phase, e.source, e.state.key()) for e in events] == [("draft", "screen", s1.key())]


def test_gsi_arriving_late_leaves_screen_only_mode():
    screen = FakeScreen(DraftState(allies=[1]))
    auto, events, clock = pilot(screen_reader=screen, gsi_grace_seconds=0)
    auto.tick()
    auto.tick()
    assert auto.screen_only and events[-1].source == "screen"
    auto.handle_gsi(payload(team=None))  # Dota aberto depois, no menu
    assert not auto.screen_only and auto.phase == "idle" and events[-1].phase == "idle"
    clock.t += 100
    auto.tick()
    assert len(screen.sides) == 2  # fora do draft o GSI manda: a tela fica parada


def test_screen_reader_exceptions_are_tolerated():
    s1 = DraftState(bans=[7])
    screen = FakeScreen(s1, RuntimeError("monitor sumiu"), s1, OSError("captura falhou"), s1, s1)
    auto, events, _ = pilot(screen_reader=screen, gsi_grace_seconds=0)
    for _ in range(6):
        auto.tick()
    assert auto.screen_errors == 2 and isinstance(auto.last_error, OSError)
    assert [e.state.key() for e in events] == [s1.key()]  # leituras separadas por erro não contam como seguidas


def test_no_duplicate_emits_and_copies_state():
    auto, events, _ = pilot()
    for _ in range(3):
        auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    events[-1].state.allies.append(99)  # a interface mexe na cópia
    auto.handle_gsi(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    assert len(events) == 1
    signatures = [(e.phase, e.side, e.state and e.state.key(), e.source) for e in events]
    assert all(a != b for a, b in zip(signatures, signatures[1:]))


def test_run_loop_until_stop():
    events = []
    stop = threading.Event()
    screen = FakeScreen(DraftState(allies=[3]))

    def on_update(update):
        events.append(update)
        stop.set()

    auto = AutoPilot(on_update, screen_reader=screen, gsi_grace_seconds=0)
    thread = threading.Thread(target=auto.run, args=(stop, 0.01), daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive() and events[0].state.allies == [3]


def test_make_screen_reader_overrides_calibrated_side():
    np = pytest.importorskip("numpy")
    from draft_assistant.screen import Calibration, Slot

    class Matcher:  # cor do recorte = id do herói
        def match(self, crop):
            value = int(crop.mean())
            return (value, 1.0) if value else (None, 0.0)

    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[:, :100], image[:, 100:] = 1, 2
    calib = Calibration(my_side="radiant", radiant_picks=[Slot(0, 0, 0.5, 1)], dire_picks=[Slot(0.5, 0, 0.5, 1)])
    read = make_screen_reader(calib, Matcher(), grab=lambda monitor: image)
    assert read("radiant").allies == [1]
    assert read("dire").allies == [2] and read("dire").enemies == [1]
    assert calib.my_side == "radiant"


def test_gsi_server_on_payload_roundtrip():
    payloads, drafts = [], []
    server = GSIServer(drafts.append, port=0, token="secret", on_payload=payloads.append)
    port = server.httpd.server_address[1]
    server.start_background()
    try:
        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", json.dumps(body).encode(), method="POST")
            urllib.request.urlopen(req, timeout=5).read()

        body = payload(SELECTION, draft=gsi_draft(radiant_picks=[1]))
        post({**body, "auth": {"token": "wrong"}})
        post({**body, "auth": {"token": "secret"}})
        post({**body, "auth": {"token": "secret"}})
        post({**payload(team=None), "auth": {"token": "secret"}})
    finally:
        server.shutdown()
    assert len(payloads) == 3 and payloads[0]["map"]["game_state"] == SELECTION
    assert len(drafts) == 1 and drafts[0].allies == [1]


def test_gsi_server_feeds_autopilot_without_on_draft():
    events = []
    auto = AutoPilot(events.append)
    server = GSIServer(None, port=0, on_payload=auto.handle_gsi)
    server.handle_payload(payload(SELECTION, draft=gsi_draft(radiant_picks=[1])))
    server.httpd.server_close()
    assert events[-1].phase == "draft" and events[-1].state.allies == [1]
