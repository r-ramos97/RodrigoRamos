"""Linha de comando: modo automático, atualizar dados, sugerir picks/bans e acompanhar o draft ao vivo."""

import argparse
import difflib
import json
import os
import re
import sys
import threading
import tomllib
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import requests

from . import coach as coach_mod
from .data_sources import JsonCache, OpenDotaClient, StratzClient
from .models import AutoUpdate, DraftState, Hero
from .scoring import Engine, MatchupTable, Suggestion, Weights

PROJECT_DIR = Path(__file__).resolve().parents[1]  # pasta do projeto (onde fica draft_assistant/)
DEFAULT_GSI_PORT = 3000
REFRESH_MAX_AGE = 24 * 3600  # heroStats mais velho que isso (s) => baixa tudo de novo
NO_DOTA_DIR = ("Não encontrei a pasta do Dota 2 nas bibliotecas da Steam. Informe o caminho, por exemplo:\n"
               '  python -m draft_assistant gsi-config --dota-dir "D:\\SteamLibrary\\steamapps\\common\\dota 2 beta"')
NEXT_STEPS = """
Próximos passos:
  1. Abra o Dota 2, entre numa tela de draft (lobby, partida contra bots ou matchmaking) e rode
       python -m draft_assistant calibrate
     para marcar os slots de pick/ban. A leitura de tela cobre o matchmaking, onde o GSI não traz o draft.
  2. Ligue o modo automático (ele detecta o draft, o seu lado e a vez sozinho):
       python -m draft_assistant auto
  3. Opcional: abrir o assistente junto com o sistema:
       python -m draft_assistant autostart-on"""

STRATZ_BRACKETS = {"herald": "HERALD_GUARDIAN", "guardian": "HERALD_GUARDIAN",
                   "crusader": "CRUSADER_ARCHON", "archon": "CRUSADER_ARCHON",
                   "legend": "LEGEND_ANCIENT", "ancient": "LEGEND_ANCIENT",
                   "divine": "DIVINE_IMMORTAL", "immortal": "DIVINE_IMMORTAL", "pro": None}

ALIASES = {
    "am": "antimage", "wk": "skeleton_king", "pa": "phantom_assassin", "pl": "phantom_lancer",
    "sf": "nevermore", "es": "earthshaker", "et": "elder_titan", "ta": "templar_assassin",
    "od": "obsidian_destroyer", "qop": "queenofpain", "cm": "crystal_maiden", "ww": "winter_wyvern",
    "wr": "windrunner", "np": "furion", "io": "wisp", "clock": "rattletrap", "timber": "shredder",
    "magnus": "magnataur", "necro": "necrolyte", "doom": "doom_bringer", "naix": "life_stealer",
    "void": "faceless_void", "sk": "sand_king", "tb": "terrorblade", "ns": "night_stalker",
    "aa": "ancient_apparition", "bs": "bloodseeker", "bb": "bristleback", "dk": "dragon_knight",
    "ck": "chaos_knight", "lc": "legion_commander", "ld": "lone_druid", "sb": "spirit_breaker",
    "shaman": "shadow_shaman", "wd": "witch_doctor", "ember": "ember_spirit", "storm": "storm_spirit",
    "underlord": "abyssal_underlord", "zeus": "zuus", "veno": "venomancer", "jugg": "juggernaut",
    "kotl": "keeper_of_the_light",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_config(path: Path | None) -> dict:
    if path and Path(path).exists():
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    return {}


def coach_profile(cfg: dict) -> coach_mod.CoachProfile:
    section = cfg.get("coach", {})
    if not section.get("stints"):
        return coach_mod.ASTINI
    stints = [coach_mod.CoachStint(
        team_name=s["team_name"],
        start=date.fromisoformat(str(s["start"])),
        end=date.fromisoformat(str(s["end"])) if s.get("end") else None,
        team_id=int(s["team_id"]) if s.get("team_id") else None,
        note=s.get("note", ""),
    ) for s in section["stints"]]
    return coach_mod.CoachProfile(section.get("name", coach_mod.ASTINI.name), stints)


class App:
    def __init__(self, args):
        self.cfg = load_config(args.config)
        self.data_dir = Path(args.data_dir)
        self.cache = JsonCache(self.data_dir / "cache")
        self.bracket = str(self.cfg.get("bracket", "divine")).lower()
        self.opendota = OpenDotaClient(self.cache, self.cfg.get("opendota_api_key") or os.environ.get("OPENDOTA_API_KEY"))
        token = self.cfg.get("stratz_token") or os.environ.get("STRATZ_TOKEN")
        self.stratz = StratzClient(self.cache, token) if token else None
        self.profile = coach_profile(self.cfg)
        self._heroes: dict[int, Hero] | None = None

    @property
    def coach_file(self) -> Path:
        return self.data_dir / f"coach_{_norm(self.profile.name)}.json"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def use_stratz(self) -> bool:
        return bool(self.stratz) and self.cfg.get("matchup_source", "auto") in ("auto", "stratz")

    def load_state(self) -> dict:
        """O que o assistente lembra entre execuções: patch dos dados, token/porta do GSI, pasta do Dota."""
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_state(self, **values) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        state = self.load_state() | values
        self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def heroes(self, offline: bool = True) -> dict[int, Hero]:
        if self._heroes is None:
            stats = self.opendota.hero_stats(offline=offline)
            if not stats:
                raise SystemExit("Sem dados locais. Rode primeiro: python -m draft_assistant update-data")
            self._heroes = {int(r["id"]): Hero(int(r["id"]), r["name"], r["localized_name"],
                                               tuple(r.get("roles") or ()), r.get("img")) for r in stats}
        return self._heroes

    def resolve(self, names: str | None) -> list[int]:
        if not names:
            return []
        heroes = self.heroes()
        by_key: dict[str, int] = {}
        for hero in heroes.values():
            by_key[_norm(hero.localized_name)] = hero.id
            by_key[_norm(hero.short_name)] = hero.id
        short_to_id = {h.short_name: h.id for h in heroes.values()}
        out = []
        for raw in names.split(","):
            raw = raw.strip()
            if not raw:
                continue
            key = _norm(raw)
            hero_id = by_key.get(key) or short_to_id.get(ALIASES.get(raw.lower(), ""))
            if hero_id is None:
                prefixed = [hid for k, hid in by_key.items() if k.startswith(key)]
                if len(set(prefixed)) == 1:
                    hero_id = prefixed[0]
            if hero_id is None:
                close = difflib.get_close_matches(key, by_key, n=3)
                hint = ", ".join(heroes[by_key[c]].localized_name for c in close)
                raise SystemExit(f"Herói não reconhecido: {raw!r}" + (f" (quis dizer: {hint}?)" if hint else ""))
            out.append(hero_id)
        return out

    def engine(self) -> Engine:
        heroes = self.heroes()
        table = MatchupTable()
        source = self.cfg.get("matchup_source", "auto")
        loaded = 0
        if self.stratz and source in ("auto", "stratz"):
            for hid in heroes:
                try:
                    adv = self.stratz.hero_matchup(hid, STRATZ_BRACKETS.get(self.bracket), offline=True)
                except (KeyError, IndexError, TypeError):
                    adv = None
                if adv:
                    table.add_stratz(hid, adv)
                    loaded += 1
        if not loaded and source in ("auto", "opendota"):
            for hid in heroes:
                rows = self.opendota.matchups(hid, offline=True)
                if rows:
                    table.add_opendota(hid, rows)
                    loaded += 1
        if not loaded:
            print("Aviso: nenhum confronto em cache; só meta/pro/coach serão usados.", file=sys.stderr)
        coach_priority = {}
        if self.cfg.get("coach", {}).get("enabled", True) and self.coach_file.exists():
            stats = json.loads(self.coach_file.read_text(encoding="utf-8"))
            coach_priority = coach_mod.priority({int(h): coach_mod.HeroCoachStat(**s) for h, s in stats.items()})
        weights = Weights(**self.cfg.get("weights", {}))
        return Engine(heroes, self.opendota.hero_stats(offline=True), table, coach_priority, weights,
                      self.bracket, coach_name=self.profile.name)


def format_suggestions(title: str, items: list[Suggestion]) -> str:
    lines = [title]
    for i, s in enumerate(items, 1):
        b = s.breakdown
        detail = (f"counter {b['counter']:+.1f} | sinergia {b['synergy']:+.1f} | meta {b['meta']:+.1f} "
                  f"| pro {b['pro']:.1f} | coach {b['coach']:.1f}")
        lines.append(f"{i:>2}. {s.hero.localized_name:<20} {s.score:+6.2f}   {detail}")
        if s.reasons:
            lines.append("      " + "; ".join(s.reasons[:4]))
    return "\n".join(lines)


def render(app: App, engine: Engine, state: DraftState, role: str | None, top: int) -> str:
    heroes = app.heroes()

    def names(ids):
        return ", ".join(heroes[i].localized_name for i in ids if i in heroes) or "-"

    header = (f"Aliados: {names(state.allies)}\nInimigos: {names(state.enemies)}\n"
              f"Banidos: {names(state.bans)}\n")
    picks = format_suggestions(f"\nPicks sugeridos{f' ({role})' if role else ''}:",
                               engine.suggest_picks(state, top, role))
    bans = format_suggestions("\nBans sugeridos:", engine.suggest_bans(state, 5))
    return header + picks + "\n" + bans


def latest_patch(patches: list[dict] | None) -> str | None:
    """Nome do patch mais recente do /constants/patch (a lista vem em ordem cronológica)."""
    return str(patches[-1]["name"]) if patches else None


def _progress(n: int, total: int) -> None:
    print(f"\rconfrontos {n}/{total}", end="" if n < total else "\n", flush=True)


def download_data(app: App, refresh: bool = False, progress: Callable[[int, int], None] | None = None,
                  stop: threading.Event | None = None) -> str | None:
    """Baixa heroStats, patch e os confrontos de todos os heróis e grava o patch em state.json.

    `refresh=True` ignora a validade do cache (e falha se a rede cair, em vez de usar o cache antigo).
    Devolve o patch mais recente; se `stop` for marcado no meio, para sem mexer no state.json.
    """
    app.opendota.hero_stats(refresh=refresh)
    app._heroes = None
    heroes = app.heroes()
    patch = latest_patch(app.opendota.patches(refresh=refresh))
    for n, hid in enumerate(heroes, 1):
        if stop is not None and stop.is_set():
            return None
        if app.use_stratz:
            app.stratz.hero_matchup(hid, STRATZ_BRACKETS.get(app.bracket), refresh=refresh)
        else:
            app.opendota.matchups(hid, refresh=refresh)
        if progress:
            progress(n, len(heroes))
    app.save_state(patch=patch, updated_at=datetime.now().isoformat(timespec="seconds"))
    return patch


def refresh_reason(app: App, max_age: float = REFRESH_MAX_AGE) -> str | None:
    """Motivo para baixar tudo de novo agora, ou None se o cache está em dia.

    Sem dados ou heroStats com mais de `max_age` s bastam; senão consulta o patch atual no OpenDota
    (uma chamada) e compara com o gravado em state.json. Falha de rede sobe como RequestException.
    """
    age = app.opendota.cache_age("/heroStats")
    if age is None:
        return "sem dados locais"
    if age > max_age:
        return f"dados com {age / 3600:.0f} h"
    newest = latest_patch(app.opendota.patches(refresh=True))
    stored = app.load_state().get("patch")
    if newest and newest != stored:
        return f"patch novo: {stored or '?'} -> {newest}"
    return None


class DataRefresher:
    """Mantém os dados em dia durante o modo automático, numa thread própria.

    A cada `check_every` s vê se os dados passaram de 24 h ou se saiu patch novo; se sim, baixa tudo
    de novo e entrega um Engine novo para `on_engine`. Sem rede, só avisa: o cache continua valendo.
    """

    check_every: float = 3600.0

    def __init__(self, app: App, on_engine: Callable[[Engine], None],
                 on_note: Callable[[str | None], None] | None = None):
        self.app = app
        self.on_engine = on_engine
        self.on_note = on_note or (lambda text: None)

    def check(self, stop: threading.Event | None = None) -> bool:
        """Uma verificação; True quando baixou dados novos e trocou o Engine."""
        try:
            reason = refresh_reason(self.app)
            if reason is None:
                self.on_note(None)
                return False
            print(f"Atualizando os dados em segundo plano ({reason})...", flush=True)
            self.on_note("atualizando dados...")
            patch = download_data(self.app, refresh=True, stop=stop)
            if stop is not None and stop.is_set():
                return False
            engine = self.app.engine()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - nada aqui pode derrubar o modo automático
            offline = isinstance(exc, requests.RequestException)
            print(f"Aviso: não deu para atualizar os dados ({exc}); seguindo com o cache.", file=sys.stderr)
            self.on_note("sem rede: usando o cache" if offline else "erro ao atualizar: usando o cache")
            return False
        self.on_engine(engine)
        self.on_note(None)
        print(f"Dados atualizados (patch {patch or '?'}).", flush=True)
        return True

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.check(stop)
            stop.wait(self.check_every)


def update_data(app: App, templates: bool = False, force: bool = False) -> None:
    patch = download_data(app, refresh=force, progress=_progress)
    heroes = app.heroes()
    print(f"{len(heroes)} heróis; patch mais recente no OpenDota: {patch or '?'}")
    if templates:
        from .screen import download_templates
        count = download_templates(heroes, app.data_dir / "templates")
        print(f"{count} retratos baixados em {app.data_dir / 'templates'}")


def cmd_update_data(app: App, args) -> None:
    update_data(app, args.templates, args.force)


def cmd_coach_teams(app: App, args) -> None:
    for row in app.opendota.explorer(coach_mod.team_search_sql(args.name)):
        print(f"{row['team_id']:>10}  {row['name']} [{row.get('tag') or ''}]  último jogo: {row.get('last_match_time')}")


def cmd_update_coach(app: App, args) -> None:
    missing = [s.team_name for s in app.profile.stints if not s.team_id]
    if missing:
        print("Sem team_id (configure em config.toml): " + ", ".join(missing), file=sys.stderr)
    stats = coach_mod.fetch_profile_drafts(app.opendota, app.profile, args.half_life)
    app.data_dir.mkdir(parents=True, exist_ok=True)
    app.coach_file.write_text(json.dumps({h: asdict(s) for h, s in stats.items()}), encoding="utf-8")
    heroes = app.heroes()
    ranked = sorted(coach_mod.priority(stats).items(), key=lambda kv: kv[1], reverse=True)[:15]
    print(f"Prioridades de draft de {app.profile.name}:")
    for hid, value in ranked:
        s = stats[hid]
        wr = f"{s.winrate:.0%}" if s.winrate is not None else "-"
        print(f"  {heroes[hid].localized_name:<20} prioridade {value:.2f}  picks {s.picks:.1f}  bans {s.bans:.1f}  WR {wr}")


def cmd_suggest(app: App, args) -> None:
    state = DraftState(app.resolve(args.allies), app.resolve(args.enemies), app.resolve(args.bans))
    print(render(app, app.engine(), state, args.role, args.top))


def cmd_watch(app: App, args) -> None:
    engine = app.engine()

    def on_draft(state: DraftState) -> None:
        print("\033[2J\033[H" + render(app, engine, state, args.role, args.top), flush=True)

    if args.source == "gsi":
        from .gsi import GSIServer
        token = args.token or app.cfg.get("gsi_token") or app.load_state().get("gsi_token")
        print(f"Aguardando GSI em http://127.0.0.1:{args.port}/ ...")
        GSIServer(on_draft, args.port, token, args.side).serve_forever()
    else:
        from .screen import Calibration, TemplateMatcher, watch
        calibration = Calibration.load(app.data_dir / "calibration.json")
        if args.side:
            calibration.my_side = args.side
        matcher = TemplateMatcher.from_directory(app.data_dir / "templates", calibration.threshold)
        print("Lendo a tela... (Ctrl+C para sair)")
        watch(calibration, matcher, on_draft, args.interval)


def configure_gsi(app: App, dota_dir: str | None, port: int, token: str | None) -> Path:
    """Grava o .cfg do GSI na pasta do Dota (achada sozinha se `dota_dir` for None) e lembra token e porta."""
    from .gsi import write_config
    from .steam_paths import find_dota_dir, launch_option_instructions

    if dota_dir:
        found = Path(dota_dir)
        if not found.is_dir():
            raise SystemExit(f"Pasta não encontrada: {found}")
    else:
        found = find_dota_dir()
        if found is None:
            raise SystemExit(NO_DOTA_DIR)
        print(f"Dota 2 encontrado em {found}")
    # Reaproveita o token já combinado, para não desencontrar do config.toml ou de um .cfg anterior.
    token = token or app.cfg.get("gsi_token") or app.load_state().get("gsi_token") or None
    path, token = write_config(found, port, token)
    app.save_state(gsi_token=token, gsi_port=port, dota_dir=str(found))
    print(f"Config gravada em {path}\nToken: {token} (o modo auto já usa este token; "
          "no watch, passe --token ou gsi_token no config.toml)")
    print(launch_option_instructions())
    return path


def cmd_gsi_config(app: App, args) -> None:
    configure_gsi(app, args.dota_dir, args.port, args.token)


def cmd_calibrate(app: App, args) -> None:
    from .screen import calibrate, save_crops
    path = app.data_dir / "calibration.json"
    app.data_dir.mkdir(parents=True, exist_ok=True)
    if args.save_crops:
        from .screen import Calibration
        for p in save_crops(Calibration.load(path), app.data_dir / "crops"):
            print(p)
        return
    calibrate(path, args.monitor, args.side or "radiant")
    print(f"Calibração salva em {path}")


class LiveDraft:
    """Liga o AutoPilot à exibição: cada AutoUpdate vira sugestões do Engine atual e um push().

    É chamado de várias threads (GSI, tela, atualização de dados); o lock mantém os pushes em ordem.
    """

    def __init__(self, display, engine: Engine, role: str | None = None, top: int = 6, bans: int = 5):
        self.display = display
        self.engine = engine
        self.role = role
        self.top = top
        self.bans = bans
        self._lock = threading.Lock()
        self._shown: AutoUpdate | None = None
        self._notes: dict[str, str] = {}

    def on_update(self, update: AutoUpdate) -> None:
        with self._lock:
            if update.phase == "idle" and self._shown is not None and self._shown.phase == "done":
                return  # o draft final fica na tela até o próximo começar
            self._shown = update
            self._push()

    def set_engine(self, engine: Engine) -> None:
        """Troca o Engine (dados novos) e redesenha a última atualização."""
        with self._lock:
            self.engine = engine
            self._push()

    def note(self, key: str, text: str | None) -> None:
        """Aviso curto exibido junto do status (ex.: "atualizando dados..."); None apaga."""
        with self._lock:
            if self._notes.get(key) == text:
                return
            if text:
                self._notes[key] = text
            else:
                self._notes.pop(key, None)
            self._push()

    def view(self, update: AutoUpdate) -> dict:
        from .ui import build_view

        picks, bans = [], []
        if update.phase == "draft":  # fora do draft não há o que sugerir
            state = update.state or DraftState()
            picks = self.engine.suggest_picks(state, self.top, self.role)
            bans = self.engine.suggest_bans(state, self.bans)
        view = build_view(update, self.engine.heroes, picks, bans)
        if self._notes:
            view["status"] = " · ".join([view["status"], *self._notes.values()])
        return view

    def _push(self) -> None:
        if self._shown is not None:
            self.display.push(self.view(self._shown))


def screen_reader(app: App, side: str | None) -> tuple[Callable[[str | None], DraftState] | None, str | None]:
    """Leitor de tela do modo automático, montado de calibration.json + data/templates.

    Devolve (leitor, problema): sem calibração, retratos ou dependências o leitor é None e o problema
    diz o que fazer. O leitor recebe o lado detectado (None = desconhecido, vale o da calibração);
    `side` sobrepõe o `my_side` gravado na calibração.
    """
    path = app.data_dir / "calibration.json"
    if not path.exists():
        return None, "falta calibrar (rode calibrate numa tela de draft)"
    from .auto import make_screen_reader

    try:
        import mss  # noqa: F401 - só confere se a captura vai funcionar
        from .screen import Calibration, TemplateMatcher
        calibration = Calibration.load(path)
        matcher = TemplateMatcher.from_directory(app.data_dir / "templates", calibration.threshold)
    except (ImportError, SystemExit):  # SystemExit: screen._cv2() sem OpenCV
        return None, "instale as dependências: pip install mss opencv-python numpy"
    except (OSError, ValueError, TypeError, KeyError):
        return None, "calibration.json inválido (rode calibrate de novo)"
    if side:
        calibration.my_side = side
    if not matcher.templates:
        return None, "sem retratos dos heróis (rode update-data --templates)"
    read = make_screen_reader(calibration, matcher)
    return (lambda detected: read(detected or calibration.my_side)), None


def cmd_auto(app: App, args) -> None:
    """Modo automático: GSI + tela, sugestões numa janela sempre no topo e dados sempre em dia.

    Só lê o jogo (GSI oficial e pixels da tela); nunca clica, digita nem escolhe herói pelo usuário.
    """
    from . import ui
    from .auto import AutoPilot
    from .gsi import GSIServer

    auto_cfg = app.cfg.get("auto", {})
    role = args.role or auto_cfg.get("role") or None
    top = args.top or int(auto_cfg.get("top") or 6)
    state = app.load_state()
    port = args.port if args.port is not None else int(state.get("gsi_port") or DEFAULT_GSI_PORT)
    token = args.token or app.cfg.get("gsi_token") or state.get("gsi_token") or None

    if app.opendota.cache_age("/heroStats") is None:
        if args.no_refresh:
            raise SystemExit("Sem dados locais. Rode primeiro: python -m draft_assistant update-data")
        print("Primeira execução: baixando os dados do OpenDota (alguns minutos, só desta vez)...")
        try:
            download_data(app, progress=_progress)
        except requests.RequestException as exc:
            raise SystemExit(f"Não deu para baixar os dados ({exc}). Confira a internet e rode de novo.") from exc
    engine = app.engine()

    display = ui.make_display(prefer_gui=not args.no_gui, geometry_file=app.data_dir / "window.json")
    live = LiveDraft(display, engine, role, top)
    pilot = AutoPilot(live.on_update, forced_side=args.side)
    reader, problem = screen_reader(app, args.side)
    if reader:
        pilot.screen_reader = lambda _side: reader(pilot.side)  # lado ainda desconhecido => o da calibração
    else:
        print(f"Aviso: leitura de tela desligada: {problem}.", file=sys.stderr)
        live.note("screen", f"tela desligada: {problem}")
    try:
        server = GSIServer(None, port, token, on_payload=pilot.handle_gsi)
    except OSError as exc:
        server = None
        message = f"não deu para abrir a porta {port} do GSI ({exc}); o assistente já está aberto?"
        if reader is None:
            display.close()
            raise SystemExit(f"Erro: {message} Sem GSI e sem leitura de tela não há como seguir o draft.")
        print(f"Aviso: {message} Seguindo só com a tela.", file=sys.stderr)

    stop = threading.Event()
    threads = [threading.Thread(target=pilot.run, args=(stop, args.interval), name="auto-pilot", daemon=True)]
    if not args.no_refresh:
        refresher = DataRefresher(app, live.set_engine, lambda text: live.note("data", text))
        threads.append(threading.Thread(target=refresher.run, args=(stop,), name="data-refresh", daemon=True))
    if server:
        server.start_background()
        print(f"Aguardando o Dota 2 (GSI em http://127.0.0.1:{server.httpd.server_address[1]}/)...")
    for thread in threads:
        thread.start()
    print("Modo automático ligado. Feche a janela ou aperte Ctrl+C para sair.", flush=True)
    try:
        display.run(stop)
    finally:
        stop.set()
        if server:
            server.shutdown()
        for thread in threads:
            thread.join(timeout=2)
    print("Assistente encerrado.")


def cmd_setup(app: App, args) -> int:
    """Primeira configuração, sem perguntas: dados + retratos, GSI e o que fazer depois."""
    ok = True
    print("[1/2] Baixando dados do OpenDota e retratos dos heróis...")
    try:
        update_data(app, templates=True)
    except requests.RequestException as exc:
        ok = False
        print(f"Aviso: não deu para baixar os dados ({exc}). Confira a internet e rode setup de novo.",
              file=sys.stderr)
    print("\n[2/2] Configurando o Game State Integration (GSI) do Dota 2...")
    try:
        configure_gsi(app, args.dota_dir, args.port, args.token)
    except SystemExit as exc:
        ok = False
        print(f"Aviso: {exc}\nSem GSI o modo automático usa só a leitura de tela.", file=sys.stderr)
    print(NEXT_STEPS)
    return 0 if ok else 1


def cmd_autostart_on(app: App, args) -> None:
    from . import autostart

    command = autostart.default_command()
    at = command.index("auto")  # opções globais vão antes do subcomando, com caminhos absolutos
    command[at:at] = ["--config", str(Path(args.config).absolute()), "--data-dir", str(app.data_dir.absolute())]
    path = autostart.install(command, PROJECT_DIR)
    print(f"Início automático ligado: {path}\n"
          "O modo automático vai abrir sozinho quando você entrar no sistema. Para desligar: autostart-off")


def cmd_autostart_off(app: App, args) -> None:
    from . import autostart

    if autostart.uninstall():
        print("Início automático desligado.")
    else:
        print("O início automático já estava desligado.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draft_assistant", description="Assistente de draft para Dota 2. "
                                     "Sem subcomando, roda o modo automático (auto).")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("auto", help="modo automático (recomendado): detecta o draft, o lado e a vez sozinho")
    p.add_argument("--no-gui", action="store_true", help="mostra as sugestões no terminal, sem janela")
    p.add_argument("--side", choices=("radiant", "dire"), help="força o lado (normalmente vem do GSI)")
    p.add_argument("--port", type=int, help=f"porta do GSI (padrão: a do gsi-config ou {DEFAULT_GSI_PORT})")
    p.add_argument("--token", help="token do GSI (padrão: gsi_token do config.toml ou o do gsi-config)")
    p.add_argument("--role", help="filtra os picks por função: Carry, Support, Initiator...")
    p.add_argument("--top", type=int, help="quantos picks sugerir (padrão 6)")
    p.add_argument("--interval", type=float, default=1.0, help="segundos entre leituras da tela")
    p.add_argument("--no-refresh", action="store_true", help="não atualiza os dados sozinho (usa só o cache)")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("setup", help="primeira configuração: dados, retratos e GSI (sem perguntas)")
    p.add_argument("--dota-dir", help="pasta do Dota 2 (padrão: procura nas bibliotecas da Steam)")
    p.add_argument("--port", type=int, default=DEFAULT_GSI_PORT)
    p.add_argument("--token")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("autostart-on", help="abre o modo automático junto com o sistema")
    p.set_defaults(func=cmd_autostart_on)
    p = sub.add_parser("autostart-off", help="desliga a abertura junto com o sistema")
    p.set_defaults(func=cmd_autostart_off)

    p = sub.add_parser("update-data", help="baixa heroStats, patch e confrontos para o cache local")
    p.add_argument("--templates", action="store_true", help="também baixa os retratos para a leitura de tela")
    p.add_argument("--force", action="store_true", help="baixa de novo mesmo com o cache dentro da validade")
    p.set_defaults(func=cmd_update_data)

    p = sub.add_parser("coach-teams", help="procura team_id de um time no OpenDota")
    p.add_argument("name")
    p.set_defaults(func=cmd_coach_teams)

    p = sub.add_parser("update-coach", help="baixa os drafts de campeonato dos times do coach")
    p.add_argument("--half-life", type=float, default=120.0, help="meia-vida (dias) do peso de cada partida")
    p.set_defaults(func=cmd_update_coach)

    for name, func, help_text in (("suggest", cmd_suggest, "sugere picks e bans para um draft digitado"),
                                  ("watch", cmd_watch, "acompanha o draft ao vivo (GSI ou tela)")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--role", help="filtra por função: Carry, Support, Initiator, Disabler, Nuker...")
        p.add_argument("--top", type=int, default=10)
        p.set_defaults(func=func)
        if name == "suggest":
            p.add_argument("--allies", default="")
            p.add_argument("--enemies", default="")
            p.add_argument("--bans", default="")
        else:
            p.add_argument("--source", choices=("gsi", "screen"), default="screen")
            p.add_argument("--side", choices=("radiant", "dire"))
            p.add_argument("--port", type=int, default=3000)
            p.add_argument("--token")
            p.add_argument("--interval", type=float, default=1.0)

    p = sub.add_parser("gsi-config", help="grava o arquivo de Game State Integration na pasta do Dota")
    p.add_argument("--dota-dir", help="padrão: procura nas bibliotecas da Steam")
    p.add_argument("--port", type=int, default=DEFAULT_GSI_PORT)
    p.add_argument("--token")
    p.set_defaults(func=cmd_gsi_config)

    p = sub.add_parser("calibrate", help="marca os slots de pick/ban na tela de draft")
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--side", choices=("radiant", "dire"))
    p.add_argument("--save-crops", action="store_true", help="salva recortes dos slots para virar templates")
    p.set_defaults(func=cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(argv)
    if args.command is None:  # sem subcomando: o modo automático é o padrão
        args = parser.parse_args([*argv, "auto"])
    try:
        return args.func(App(args), args) or 0
    except KeyboardInterrupt:
        print("\nInterrompido.", file=sys.stderr)
        return 130
    except requests.RequestException as exc:
        print(f"Erro de rede ao falar com a API ({type(exc).__name__}). Confira a internet e tente de novo; "
              "o que já estava baixado continua salvo.", file=sys.stderr)
        return 2
