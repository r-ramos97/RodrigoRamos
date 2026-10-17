from draft_assistant.models import DraftState
from draft_assistant.scoring import Engine, MatchupTable, Weights
from fixtures import HERO_STATS, MATCHUPS, heroes


def engine(coach=None, weights=None):
    table = MatchupTable()
    for hid, rows in MATCHUPS.items():
        table.add_opendota(hid, rows)
    return Engine(heroes(), HERO_STATS, table, coach, weights or Weights(), "divine")


def test_pairs_are_symmetric_and_not_double_counted():
    table = MatchupTable()
    for hid, rows in MATCHUPS.items():
        table.add_opendota(hid, rows)
    assert table.vs[(1, 2)] == (400, 140)
    assert table.vs[(2, 1)] == (400, 260)


def test_counter_to_anti_mage_is_axe():
    picks = engine(weights=Weights(meta=0, pro=0, coach=0)).suggest_picks(DraftState(enemies=[1]), top=4)
    assert picks[0].hero.localized_name == "Axe"
    assert picks[0].breakdown["counter"] > 10
    assert any("vs Anti-Mage" in r for r in picks[0].reasons)
    # Bloodseeker perde para AM -> fica por último
    assert picks[-1].hero.localized_name == "Bloodseeker"


def test_small_samples_are_shrunk():
    eng = engine(weights=Weights(meta=0, pro=0, coach=0))
    cm = next(s for s in eng.suggest_picks(DraftState(enemies=[1]), top=4) if s.hero.id == 5)
    # 0 vitórias em 3 jogos para a CM, mas a suavização mantém o efeito pequeno
    assert -5 < cm.breakdown["counter"] < 0


def test_taken_heroes_and_role_filter():
    picks = engine().suggest_picks(DraftState(allies=[3], enemies=[1], bans=[2]), role="Support")
    ids = [p.hero.id for p in picks]
    assert ids == [5]


def test_coach_priority_moves_hero_up():
    neutral = Weights(counter=0, synergy=0, meta=0, pro=0, coach=1)
    picks = engine(coach={4: 1.0}, weights=neutral).suggest_picks(DraftState(), top=1)
    assert picks[0].hero.id == 4
    assert any("prioridade" in r for r in picks[0].reasons)


def test_ban_suggestions_target_counters_of_our_picks():
    bans = engine(weights=Weights(meta=0, pro=0, coach=0)).suggest_bans(DraftState(allies=[1]), top=1)
    assert bans[0].hero.id == 2


def test_stratz_synergy():
    table = MatchupTable()
    table.add_stratz(3, {"vs": [], "with": [{"heroId2": 5, "matchCount": 500, "winCount": 300}]})
    eng = Engine(heroes(), HERO_STATS, table, weights=Weights(counter=0, meta=0, pro=0, coach=0))
    top = eng.suggest_picks(DraftState(allies=[5]), top=1)[0]
    assert top.hero.id == 3 and top.breakdown["synergy"] > 5
