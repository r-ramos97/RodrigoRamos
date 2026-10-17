import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from draft_assistant.screen import Calibration, Slot, TemplateMatcher, read_draft  # noqa: E402


def portrait(seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (9, 16, 3), dtype=np.uint8)
    return cv2.resize(small, (128, 72), interpolation=cv2.INTER_NEAREST)


def test_reads_draft_from_synthetic_screenshot():
    templates = {hid: [portrait(hid)] for hid in range(1, 8)}
    screen = np.full((1080, 1920, 3), 30, dtype=np.uint8)
    slots = {}
    for name, hid, x in (("r0", 1, 100), ("r1", 4, 260), ("d0", 6, 1500), ("b0", 7, 900)):
        tile = cv2.resize(portrait(hid), (96, 54), interpolation=cv2.INTER_AREA)  # escala diferente do template
        screen[20:74, x:x + 96] = tile
        slots[name] = Slot((x - 6) / 1920, 14 / 1080, 108 / 1920, 66 / 1080)
    empty = Slot(700 / 1920, 300 / 1080, 108 / 1920, 66 / 1080)
    calib = Calibration(my_side="dire", radiant_picks=[slots["r0"], slots["r1"], empty],
                        dire_picks=[slots["d0"]], bans=[slots["b0"]])
    state = read_draft(screen, calib, TemplateMatcher(templates, threshold=0.6))
    assert state.enemies == [1, 4]
    assert state.allies == [6]
    assert state.bans == [7]


def test_calibration_roundtrip(tmp_path):
    calib = Calibration(radiant_picks=[Slot(0.1, 0.0, 0.05, 0.04)])
    calib.save(tmp_path / "c.json")
    assert Calibration.load(tmp_path / "c.json").radiant_picks[0].x == 0.1
