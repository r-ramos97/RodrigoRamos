from draft_assistant.cli import main
from draft_assistant.data_sources import JsonCache
from fixtures import HERO_STATS, MATCHUPS


def seed(tmp_path):
    cache = JsonCache(tmp_path / "cache")
    cache.put("opendota:/heroStats:{}", HERO_STATS)
    for hid, rows in MATCHUPS.items():
        cache.put(f"opendota:/heroes/{hid}/matchups:{{}}", rows)


def test_suggest_offline(tmp_path, capsys):
    seed(tmp_path)
    main(["--config", str(tmp_path / "none.toml"), "--data-dir", str(tmp_path),
          "suggest", "--enemies", "am", "--top", "3"])
    out = capsys.readouterr().out
    assert "Inimigos: Anti-Mage" in out
    assert out.split("Picks sugeridos:")[1].strip().startswith("1. Axe")


def test_unknown_hero_hint(tmp_path):
    seed(tmp_path)
    import pytest
    with pytest.raises(SystemExit, match="Crystal Maiden"):
        main(["--config", str(tmp_path / "none.toml"), "--data-dir", str(tmp_path),
              "suggest", "--enemies", "cristal maiden"])
