"""Modo automático: acompanha a partida sozinho e avisa a interface a cada mudança do draft.

"Automático" quer dizer que o assistente detecta sozinho quando o draft começa e termina, de
que lado você está e quais heróis já saíram. Ele só LÊ (GSI oficial da Valve ou pixels da
tela): nunca clica, digita, escolhe herói ou envia qualquer comando ao jogo.

Fontes, em ordem de preferência:
- GSI: dá as fases da partida (menu, draft, jogo), o lado e, quando vem preenchido, o draft;
- tela: usada durante o draft quando o GSI não traz picks/bans (comum em matchmaking);
- só tela: se nenhum payload do GSI chegar na carência inicial (GSI não configurado), a tela
  é lida o tempo todo e só leituras não vazias e estáveis são publicadas.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from .gsi import parse_draft, parse_turn
from .models import AutoUpdate, DraftState

DRAFT_STATES = frozenset({"DOTA_GAMERULES_STATE_HERO_SELECTION", "DOTA_GAMERULES_STATE_STRATEGY_TIME"})
SIDES = ("radiant", "dire")


class AutoPilot:
    """Transforma o fluxo do GSI (+ leitor de tela opcional) em eventos `AutoUpdate`.

    `handle_gsi` vai no `on_payload` do `GSIServer`; `run` (ou `tick` periódico) cuida da tela.
    Mudanças só de `seconds_left` não geram evento; mudanças de vez (minha/deles, pick/ban) geram.
    """

    gsi_stale_seconds: float = 5.0  # sem picks/bans do GSI há mais que isso => lê a tela

    def __init__(self, on_update: Callable[[AutoUpdate], None],
                 screen_reader: Callable[[str], DraftState] | None = None,
                 forced_side: str | None = None, gsi_grace_seconds: float = 20.0,
                 clock: Callable[[], float] = time.monotonic):
        forced_side = forced_side.lower() if forced_side else None
        if forced_side is not None and forced_side not in SIDES:
            raise ValueError("forced_side deve ser 'radiant' ou 'dire'")
        self.on_update = on_update
        self.screen_reader = screen_reader
        self.forced_side = forced_side
        self.gsi_grace_seconds = gsi_grace_seconds
        self.clock = clock
        self.screen_errors = 0  # exceções do leitor de tela (engolidas para não derrubar o loop)
        self.last_error: Exception | None = None
        self._lock = threading.RLock()
        self._started = clock()
        self._phase = "idle"
        self._side = forced_side
        self._state: DraftState | None = None
        self._turn = None
        self._source: str | None = None
        self._draft_id = 0  # muda a cada draft novo; descarta leituras de tela atrasadas
        self._gsi_seen = False
        self._gsi_draft_at: float | None = None  # último payload do GSI com picks/bans
        self._screen_only = False
        self._pending = None  # chave da leitura de tela anterior (debounce)
        self._last_signature = None

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def side(self) -> str | None:
        with self._lock:
            return self._side

    @property
    def screen_only(self) -> bool:
        """True quando o GSI não deu sinal na carência inicial e só a tela está sendo usada."""
        with self._lock:
            return self._screen_only

    def handle_gsi(self, payload: dict) -> None:
        with self._lock:
            now = self.clock()
            if self._screen_only:  # o GSI apareceu depois (ex.: Dota aberto mais tarde): ele volta a mandar
                self._screen_only = False
                self._phase = "idle"
            self._gsi_seen = True
            team = str((payload.get("player") or {}).get("team_name") or "").lower()
            if self.forced_side is None and team in SIDES:
                self._side = team
            game_state = (payload.get("map") or {}).get("game_state")
            if game_state not in DRAFT_STATES:
                if self._phase == "draft":
                    self._phase, self._turn = "done", None
                else:
                    self._phase, self._state, self._turn, self._source = "idle", None, None, None
                self._publish()
                return
            if self._phase != "draft":
                self._start_draft()
                self._source = "gsi"
            state = parse_draft(payload, self._side)
            if state is not None and state.unavailable:
                self._gsi_draft_at = now
                if state.key() != self._state.key():
                    self._state, self._source = state, "gsi"
            self._turn = parse_turn(payload, self._side)
            self._publish()

    def tick(self) -> None:
        """Uma rodada da leitura de tela; não faz nada se o GSI já está trazendo o draft."""
        with self._lock:
            if (not self._gsi_seen and not self._screen_only
                    and self.clock() - self._started >= self.gsi_grace_seconds):
                self._screen_only = True
                self._start_draft()
            if not self._wants_screen():
                self._pending = None
                return
            side, draft_id = self._side or "radiant", self._draft_id
        try:
            state = self.screen_reader(side)  # fora do lock: a captura pode demorar
        except Exception as exc:  # noqa: BLE001 - qualquer falha da captura não pode parar o modo automático
            with self._lock:
                self.screen_errors += 1
                self.last_error = exc
                self._pending = None
            return
        with self._lock:
            if draft_id != self._draft_id or not self._wants_screen():
                return
            key = state.key()
            if key == self._pending and key != self._state.key() and state.unavailable:
                self._state, self._source = state, "screen"
                self._publish()
            self._pending = key

    def run(self, stop: threading.Event, interval: float = 1.0) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(interval)

    def _start_draft(self) -> None:
        self._phase = "draft"
        self._draft_id += 1
        self._state, self._turn, self._source = DraftState(), None, None
        self._gsi_draft_at, self._pending = None, None

    def _wants_screen(self) -> bool:
        if self.screen_reader is None or self._phase != "draft":
            return False
        return self._gsi_draft_at is None or self.clock() - self._gsi_draft_at > self.gsi_stale_seconds

    def _publish(self) -> None:
        if self._phase == "draft":
            turn = (self._turn.my_turn, self._turn.is_pick) if self._turn else None
            signature = (self._phase, self._side, self._state.key(), turn, self._source)
        else:
            signature = (self._phase,)  # "done" e "idle" saem uma vez só
        if signature == self._last_signature:
            return
        self._last_signature = signature
        state = self._state
        if state is not None:  # cópia: a interface pode mexer nas listas sem afetar a comparação
            state = DraftState(list(state.allies), list(state.enemies), list(state.bans))
        self.on_update(AutoUpdate(self._phase, self._side, state, self._turn, self._source))


def make_screen_reader(calibration, matcher, grab=None) -> Callable[[str], DraftState]:
    """Leitor de tela para o `AutoPilot`: captura o monitor calibrado e lê o draft do lado pedido."""
    from . import screen

    grab = grab or screen.grab

    def read(side: str) -> DraftState:
        return screen.read_draft(grab(calibration.monitor), replace(calibration, my_side=side), matcher)

    return read
