"""Game State Integration (GSI): a integração oficial da Valve que envia o estado do jogo por HTTP.

O bloco `draft` vem preenchido com certeza quando você assiste/transmite uma partida (e em
lobbies Captains Mode); jogando em matchmaking ele pode chegar vazio. Nesse caso use a leitura
de tela (`screen.py`) como fonte do draft.
"""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .models import DraftState

DEFAULT_DOTA_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta")
CFG_SUBDIR = Path("game/dota/cfg/gamestate_integration")
CFG_NAME = "gamestate_integration_draftassistant.cfg"

TEAM_KEYS = {"radiant": "team2", "dire": "team3"}


def config_text(port: int, token: str) -> str:
    return f'''"Dota Draft Assistant"
{{
    "uri"           "http://127.0.0.1:{port}/"
    "timeout"       "5.0"
    "buffer"        "0.1"
    "throttle"      "0.1"
    "heartbeat"     "30.0"
    "data"
    {{
        "provider"  "1"
        "map"       "1"
        "player"    "1"
        "hero"      "1"
        "draft"     "1"
    }}
    "auth"
    {{
        "token"     "{token}"
    }}
}}
'''


def write_config(dota_dir: Path = DEFAULT_DOTA_DIR, port: int = 3000, token: str | None = None) -> tuple[Path, str]:
    """Grava o .cfg na pasta do Dota. Lembre de adicionar `-gamestateintegration` nas opções de inicialização."""
    token = token or secrets.token_hex(16)
    target = Path(dota_dir) / CFG_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / CFG_NAME
    path.write_text(config_text(port, token), encoding="utf-8")
    return path, token


def _team_heroes(team: dict, kind: str, slots: int) -> list[int]:
    out = []
    for i in range(slots):
        hero_id = int(team.get(f"{kind}{i}_id") or 0)
        if hero_id > 0:
            out.append(hero_id)
    return out


def parse_draft(payload: dict, my_side: str | None = None) -> DraftState | None:
    """Converte o JSON do GSI em DraftState. `my_side` ('radiant'/'dire') sobrepõe o lado detectado."""
    draft = payload.get("draft")
    if not draft:
        return None
    side = (my_side or payload.get("player", {}).get("team_name") or "").lower()
    if side not in TEAM_KEYS:
        # Assistindo: o time "home" é tratado como aliado; sem essa informação, assume Radiant.
        side = "dire" if draft.get("team3", {}).get("home_team") else "radiant"
    other = "dire" if side == "radiant" else "radiant"
    mine, theirs = draft.get(TEAM_KEYS[side], {}), draft.get(TEAM_KEYS[other], {})
    if not mine and not theirs:
        return None
    return DraftState(
        allies=_team_heroes(mine, "pick", 5),
        enemies=_team_heroes(theirs, "pick", 5),
        bans=_team_heroes(mine, "ban", 7) + _team_heroes(theirs, "ban", 7),
    )


class GSIServer:
    """Servidor HTTP local que recebe o GSI e chama `on_draft` quando o draft muda."""

    def __init__(self, on_draft, port: int = 3000, token: str | None = None, my_side: str | None = None):
        self.on_draft = on_draft
        self.token = token
        self.my_side = my_side
        self._last_key = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                server.handle_payload(payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    def handle_payload(self, payload: dict) -> None:
        if self.token and payload.get("auth", {}).get("token") != self.token:
            return
        state = parse_draft(payload, self.my_side)
        if state is None or state.key() == self._last_key:
            return
        self._last_key = state.key()
        self.on_draft(state)

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
