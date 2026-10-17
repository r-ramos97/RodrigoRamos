from draft_assistant.models import Hero

# 1 Anti-Mage, 2 Axe, 3 Bane, 4 Bloodseeker, 5 Crystal Maiden
HERO_STATS = [
    {"id": 1, "name": "npc_dota_hero_antimage", "localized_name": "Anti-Mage", "roles": ["Carry", "Escape"],
     "img": "/apps/dota2/images/dota_react/heroes/antimage.png?", "7_pick": 1000, "7_win": 480,
     "pro_pick": 10, "pro_ban": 5},
    {"id": 2, "name": "npc_dota_hero_axe", "localized_name": "Axe", "roles": ["Initiator", "Durable"],
     "img": "/apps/dota2/images/dota_react/heroes/axe.png?", "7_pick": 1000, "7_win": 520,
     "pro_pick": 40, "pro_ban": 30},
    {"id": 3, "name": "npc_dota_hero_bane", "localized_name": "Bane", "roles": ["Support", "Disabler"],
     "img": "/apps/dota2/images/dota_react/heroes/bane.png?", "7_pick": 1000, "7_win": 500,
     "pro_pick": 5, "pro_ban": 0},
    {"id": 4, "name": "npc_dota_hero_bloodseeker", "localized_name": "Bloodseeker", "roles": ["Carry"],
     "img": "/apps/dota2/images/dota_react/heroes/bloodseeker.png?", "7_pick": 1000, "7_win": 500,
     "pro_pick": 0, "pro_ban": 0},
    {"id": 5, "name": "npc_dota_hero_crystal_maiden", "localized_name": "Crystal Maiden",
     "roles": ["Support", "Nuker"], "img": "/apps/dota2/images/dota_react/heroes/crystal_maiden.png?",
     "7_pick": 1000, "7_win": 500, "pro_pick": 2, "pro_ban": 0},
]

# /heroes/1/matchups: Anti-Mage vs outros. wins = vitórias do Anti-Mage.
MATCHUPS = {
    1: [{"hero_id": 2, "games_played": 400, "wins": 140},   # AM perde muito para Axe
        {"hero_id": 3, "games_played": 400, "wins": 200},
        {"hero_id": 4, "games_played": 400, "wins": 230},
        {"hero_id": 5, "games_played": 3, "wins": 3}],      # amostra minúscula
    # Lista do Axe repete o par (2,1) e não pode ser somada de novo.
    2: [{"hero_id": 1, "games_played": 400, "wins": 260}],
}


def heroes() -> dict[int, Hero]:
    return {r["id"]: Hero(r["id"], r["name"], r["localized_name"], tuple(r["roles"]), r["img"]) for r in HERO_STATS}
