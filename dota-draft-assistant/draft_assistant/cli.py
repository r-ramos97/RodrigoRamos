"""Linha de comando: atualizar dados, sugerir picks/bans e acompanhar o draft ao vivo."""

import argparse
import difflib
import json
import os
import re
import sys
import tomllib
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import coach as coach_mod
from .data_sources import JsonCache, OpenDotaClient, StratzClient
from .models import DraftState, Hero
from .scoring import Engine, MatchupTable, Suggestion, Weights

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


def cmd_update_data(app: App, args) -> None:
    stats = app.opendota.hero_stats()
    app._heroes = None
    heroes = app.heroes()
    patches = app.opendota.patches()
    print(f"{len(stats)} heróis; patch mais recente no OpenDota: {patches[-1]['name'] if patches else '?'}")
    for n, hid in enumerate(heroes, 1):
        if app.stratz and app.cfg.get("matchup_source", "auto") in ("auto", "stratz"):
            app.stratz.hero_matchup(hid, STRATZ_BRACKETS.get(app.bracket))
        else:
            app.opendota.matchups(hid)
        print(f"\rconfrontos {n}/{len(heroes)}", end="", flush=True)
    print()
    if args.templates:
        from .screen import download_templates
        count = download_templates(heroes, app.data_dir / "templates")
        print(f"{count} retratos baixados em {app.data_dir / 'templates'}")


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
        token = args.token or app.cfg.get("gsi_token")
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


def cmd_gsi_config(app: App, args) -> None:
    from .gsi import DEFAULT_DOTA_DIR, write_config
    path, token = write_config(Path(args.dota_dir) if args.dota_dir else DEFAULT_DOTA_DIR, args.port, args.token)
    print(f"Config gravada em {path}\nToken: {token} (use em --token ou gsi_token no config.toml)")
    print("Adicione -gamestateintegration nas opções de inicialização do Dota 2 na Steam.")


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draft_assistant", description="Assistente de draft para Dota 2")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--data-dir", default="data")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("update-data", help="baixa heroStats, patch e confrontos para o cache local")
    p.add_argument("--templates", action="store_true", help="também baixa os retratos para a leitura de tela")
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
    p.add_argument("--dota-dir")
    p.add_argument("--port", type=int, default=3000)
    p.add_argument("--token")
    p.set_defaults(func=cmd_gsi_config)

    p = sub.add_parser("calibrate", help="marca os slots de pick/ban na tela de draft")
    p.add_argument("--monitor", type=int, default=1)
    p.add_argument("--side", choices=("radiant", "dire"))
    p.add_argument("--save-crops", action="store_true", help="salva recortes dos slots para virar templates")
    p.set_defaults(func=cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(App(args), args)
    return 0
