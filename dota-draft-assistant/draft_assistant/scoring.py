"""Motor de sugestão: counter, sinergia, meta, presença pro e perfil do coach.

Cada componente é expresso em pontos percentuais (pp) de win rate, para que os pesos sejam
comparáveis. Um confronto com poucas partidas é encolhido para a win rate base do herói
(suavização bayesiana), assim 3 vitórias em 3 jogos não viram "100% de counter".
"""

from dataclasses import dataclass, field

from .models import DraftState, Hero

BRACKETS = {"herald": 1, "guardian": 2, "crusader": 3, "archon": 4,
            "legend": 5, "ancient": 6, "divine": 7, "immortal": 8, "pro": "pro"}


@dataclass
class Weights:
    counter: float = 1.0
    synergy: float = 0.5
    meta: float = 0.4
    pro: float = 0.3
    coach: float = 0.5
    prior_games: float = 60.0  # partidas "fictícias" na suavização de cada confronto


@dataclass
class Suggestion:
    hero: Hero
    score: float
    breakdown: dict[str, float]
    reasons: list[str] = field(default_factory=list)


class MatchupTable:
    """Guarda (partidas, vitórias de a) para cada par (a, b), nas duas direções."""

    def __init__(self):
        self.vs: dict[tuple[int, int], tuple[int, int]] = {}
        self.with_: dict[tuple[int, int], tuple[int, int]] = {}
        # A mesma partida aparece na lista de A e na de B; guardamos o par só uma vez.
        self._seen: set[tuple[int, int]] = set()
        self._seen_with: set[tuple[int, int]] = set()

    def add_vs(self, a: int, b: int, games: int, wins_a: int) -> None:
        if games <= 0:
            return
        g, w = self.vs.get((a, b), (0, 0))
        self.vs[(a, b)] = (g + games, w + wins_a)
        g, w = self.vs.get((b, a), (0, 0))
        self.vs[(b, a)] = (g + games, w + games - wins_a)

    def add_with(self, a: int, b: int, games: int, wins: int) -> None:
        if games <= 0:
            return
        for pair in ((a, b), (b, a)):
            g, w = self.with_.get(pair, (0, 0))
            self.with_[pair] = (g + games, w + wins)

    def add_opendota(self, hero_id: int, rows: list[dict]) -> None:
        """Linhas de /heroes/{hero_id}/matchups."""
        for row in rows:
            other = int(row["hero_id"])
            if (other, hero_id) in self._seen:
                continue
            self._seen.add((hero_id, other))
            self.add_vs(hero_id, other, int(row["games_played"]), int(row["wins"]))

    def add_stratz(self, hero_id: int, advantage: dict) -> None:
        for row in advantage.get("vs", []):
            other = int(row["heroId2"])
            if (other, hero_id) in self._seen:
                continue
            self._seen.add((hero_id, other))
            self.add_vs(hero_id, other, int(row["matchCount"]), int(row["winCount"]))
        for row in advantage.get("with", []):
            other = int(row["heroId2"])
            if (other, hero_id) in self._seen_with:
                continue
            self._seen_with.add((hero_id, other))
            self.add_with(hero_id, other, int(row["matchCount"]), int(row["winCount"]))


def base_winrates(hero_stats: list[dict], bracket) -> dict[int, float]:
    out = {}
    for row in hero_stats:
        picks, wins = row.get(f"{bracket}_pick") or 0, row.get(f"{bracket}_win") or 0
        out[int(row["id"])] = wins / picks if picks else 0.5
    return out


def pro_presence(hero_stats: list[dict]) -> dict[int, float]:
    """(picks + bans) no cenário pro, normalizado para 0..1."""
    raw = {int(r["id"]): (r.get("pro_pick") or 0) + (r.get("pro_ban") or 0) for r in hero_stats}
    top = max(raw.values(), default=0) or 1
    return {hid: v / top for hid, v in raw.items()}


class Engine:
    def __init__(self, heroes: dict[int, Hero], hero_stats: list[dict], matchups: MatchupTable,
                 coach_priority: dict[int, float] | None = None, weights: Weights | None = None,
                 bracket="divine", coach_name: str = "coach"):
        self.heroes = heroes
        self.matchups = matchups
        self.weights = weights or Weights()
        self.base = base_winrates(hero_stats, BRACKETS.get(str(bracket).lower(), bracket))
        self.presence = pro_presence(hero_stats)
        self.coach = coach_priority or {}
        self.coach_name = coach_name

    def _delta(self, table: dict, a: int, b: int, prior: float) -> tuple[float, int]:
        games, wins = table.get((a, b), (0, 0))
        k = self.weights.prior_games
        return (wins - games * prior) / (games + k), games

    def _score(self, hero_id: int, against: list[int], along: list[int]) -> Suggestion:
        w = self.weights
        base = self.base.get(hero_id, 0.5)
        counter_pp, synergy_pp, reasons = 0.0, 0.0, []
        for enemy in against:
            delta, games = self._delta(self.matchups.vs, hero_id, enemy, base)
            counter_pp += delta * 100
            if games and abs(delta) * 100 >= 1.0:
                sign = "+" if delta > 0 else ""
                reasons.append(f"{sign}{delta * 100:.1f}pp vs {self.heroes[enemy].localized_name}"
                               if enemy in self.heroes else f"{sign}{delta * 100:.1f}pp vs #{enemy}")
        for ally in along:
            pair_base = (base + self.base.get(ally, 0.5)) / 2
            delta, games = self._delta(self.matchups.with_, hero_id, ally, pair_base)
            synergy_pp += delta * 100
            if games and delta * 100 >= 1.0 and ally in self.heroes:
                reasons.append(f"+{delta * 100:.1f}pp com {self.heroes[ally].localized_name}")
        breakdown = {
            "counter": counter_pp,
            "synergy": synergy_pp,
            "meta": (base - 0.5) * 100,
            "pro": self.presence.get(hero_id, 0.0) * 5,
            "coach": self.coach.get(hero_id, 0.0) * 5,
        }
        if self.coach.get(hero_id, 0) >= 0.5:
            reasons.append(f"prioridade nos drafts de {self.coach_name}")
        score = sum(getattr(w, name) * value for name, value in breakdown.items())
        return Suggestion(self.heroes[hero_id], score, breakdown, reasons)

    def _candidates(self, state: DraftState, role: str | None) -> list[int]:
        taken = state.unavailable
        out = []
        for hid, hero in self.heroes.items():
            if hid in taken:
                continue
            if role and role.lower() not in (r.lower() for r in hero.roles):
                continue
            out.append(hid)
        return out

    def suggest_picks(self, state: DraftState, top: int = 10, role: str | None = None) -> list[Suggestion]:
        ranked = [self._score(h, state.enemies, state.allies) for h in self._candidates(state, role)]
        return sorted(ranked, key=lambda s: s.score, reverse=True)[:top]

    def suggest_bans(self, state: DraftState, top: int = 5) -> list[Suggestion]:
        """Heróis que o inimigo escolheria contra nós: counters dos nossos picks + meta + pro + coach."""
        ranked = [self._score(h, state.allies, state.enemies) for h in self._candidates(state, None)]
        return sorted(ranked, key=lambda s: s.score, reverse=True)[:top]
