"""Perfil de coach: usa os drafts de campeonato dos times de um coach como referência.

O perfil padrão é o do brasileiro Filipe "Astini" Astini. As passagens abaixo vêm de notícias
públicas (GosuGamers, Hawk Live, esports.ru); as datas são aproximadas e os team_id precisam
ser confirmados com `python -m draft_assistant coach-teams <nome>`, porque vários times
dividem o mesmo nome no OpenDota.
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .data_sources import OpenDotaClient


@dataclass
class CoachStint:
    team_name: str
    start: date
    end: date | None = None  # None = passagem atual
    team_id: int | None = None
    note: str = ""


@dataclass
class CoachProfile:
    name: str
    stints: list[CoachStint] = field(default_factory=list)


ASTINI = CoachProfile(
    name='Filipe "Astini" Astini',
    stints=[
        CoachStint("FURIA", date(2019, 1, 1), date(2019, 12, 31), note="primeiro time de destaque"),
        CoachStint("nouns", date(2023, 1, 1), date(2023, 12, 31), note="top 8 no TI 2023"),
        CoachStint("Cloud9", date(2024, 1, 1), date(2024, 9, 30), note="top 6 no TI 2024"),
        CoachStint("PARIVISION", date(2024, 10, 1), date(2026, 2, 28), note="temporada de estreia em 2025"),
        CoachStint("Team Nemesis", date(2026, 3, 1), None, note="EWC 2026"),
    ],
)


@dataclass
class HeroCoachStat:
    picks: float = 0.0
    bans: float = 0.0
    wins: float = 0.0
    first_phase: float = 0.0  # picks/bans nas primeiras posições do draft (prioridade)
    games: int = 0

    @property
    def winrate(self) -> float | None:
        return self.wins / self.picks if self.picks else None


def team_search_sql(name: str) -> str:
    safe = name.replace("'", "''")
    return (f"SELECT t.team_id, t.name, t.tag, r.rating, r.last_match_time "
            f"FROM teams t LEFT JOIN team_rating r USING(team_id) "
            f"WHERE t.name ILIKE '%{safe}%' OR t.tag ILIKE '%{safe}%' "
            f"ORDER BY r.last_match_time DESC NULLS LAST LIMIT 20")


def drafts_sql(team_id: int, start: date, end: date | None) -> str:
    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    end_ts = (int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
              if end else int(time.time()) + 86400)
    tid = int(team_id)
    return (
        "SELECT m.match_id, m.start_time, m.radiant_team_id, m.dire_team_id, m.radiant_win, "
        "pb.hero_id, pb.is_pick, pb.team, pb.ord "
        "FROM matches m JOIN picks_bans pb USING(match_id) "
        f"WHERE (m.radiant_team_id = {tid} OR m.dire_team_id = {tid}) "
        f"AND m.start_time >= {start_ts} AND m.start_time < {end_ts} "
        "ORDER BY m.match_id DESC LIMIT 10000"
    )


def aggregate_drafts(rows: list[dict], team_id: int, now: float | None = None,
                     half_life_days: float = 120.0, first_phase_ord: int = 8) -> dict[int, HeroCoachStat]:
    """Soma os picks e bans feitos pelo time do coach, com decaimento por idade da partida.

    `picks_bans.team` é 0 para Radiant e 1 para Dire; só contam as ações do lado do time.
    """
    now = now or time.time()
    stats: dict[int, HeroCoachStat] = {}
    seen_games: dict[int, set[int]] = {}
    for row in rows:
        side = 0 if row["radiant_team_id"] == team_id else 1
        if row["team"] != side:
            continue
        age_days = max(0.0, (now - row["start_time"]) / 86400)
        weight = 0.5 ** (age_days / half_life_days)
        stat = stats.setdefault(int(row["hero_id"]), HeroCoachStat())
        if row["is_pick"]:
            stat.picks += weight
            won = bool(row["radiant_win"]) == (side == 0)
            stat.wins += weight if won else 0.0
        else:
            stat.bans += weight
        if row["ord"] is not None and row["ord"] < first_phase_ord:
            stat.first_phase += weight
        seen_games.setdefault(int(row["hero_id"]), set()).add(row["match_id"])
    for hero_id, matches in seen_games.items():
        stats[hero_id].games = len(matches)
    return stats


def priority(stats: dict[int, HeroCoachStat]) -> dict[int, float]:
    """0..1: picks valem 1, bans 0.5 e cada ação na primeira fase soma mais 0.5."""
    raw = {h: s.picks + 0.5 * s.bans + 0.5 * s.first_phase for h, s in stats.items()}
    top = max(raw.values(), default=0) or 1
    return {h: v / top for h, v in raw.items()}


def fetch_profile_drafts(client: OpenDotaClient, profile: CoachProfile,
                         half_life_days: float = 120.0) -> dict[int, HeroCoachStat]:
    """Baixa os drafts de todas as passagens com team_id definido e junta num só perfil."""
    merged: dict[int, HeroCoachStat] = {}
    for stint in profile.stints:
        if not stint.team_id:
            continue
        rows = client.explorer(drafts_sql(stint.team_id, stint.start, stint.end))
        for hero_id, stat in aggregate_drafts(rows, stint.team_id, half_life_days=half_life_days).items():
            m = merged.setdefault(hero_id, HeroCoachStat())
            m.picks += stat.picks
            m.bans += stat.bans
            m.wins += stat.wins
            m.first_phase += stat.first_phase
            m.games += stat.games
    return merged
