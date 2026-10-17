import time

from draft_assistant.coach import ASTINI, aggregate_drafts, drafts_sql, priority, team_search_sql

NOW = time.time()


def row(match_id, hero_id, is_pick, team, ord_, radiant_win=True, radiant_team=100, dire_team=200, age_days=0):
    return {"match_id": match_id, "start_time": NOW - age_days * 86400, "radiant_team_id": radiant_team,
            "dire_team_id": dire_team, "radiant_win": radiant_win, "hero_id": hero_id,
            "is_pick": is_pick, "team": team, "ord": ord_}


def test_only_coach_team_actions_count():
    rows = [
        row(1, 10, True, 0, 4),            # pick do time 100 (Radiant), venceu
        row(1, 11, False, 0, 0),           # ban do time 100
        row(1, 12, True, 1, 5),            # pick do adversário: ignorado
        row(2, 10, True, 1, 9, radiant_win=True, radiant_team=300, dire_team=100),  # time 100 de Dire, perdeu
    ]
    stats = aggregate_drafts(rows, 100, now=NOW)
    assert set(stats) == {10, 11}
    assert stats[10].picks == 2 and stats[10].wins == 1 and stats[10].games == 2
    assert stats[11].bans == 1
    assert priority(stats)[10] == 1.0


def test_old_matches_decay():
    rows = [row(1, 10, True, 0, 9, age_days=0), row(2, 20, True, 0, 9, age_days=240)]
    stats = aggregate_drafts(rows, 100, now=NOW, half_life_days=120)
    assert abs(stats[20].picks - 0.25) < 1e-6


def test_sql_is_sanitized():
    assert "''" in team_search_sql("O'Neil")
    sql = drafts_sql(123, ASTINI.stints[-1].start, None)
    assert "radiant_team_id = 123" in sql and "picks_bans" in sql


def test_astini_profile_has_current_team():
    assert ASTINI.stints[-1].team_name == "Team Nemesis" and ASTINI.stints[-1].end is None
