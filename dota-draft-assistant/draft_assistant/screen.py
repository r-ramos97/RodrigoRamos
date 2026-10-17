"""Leitura do draft pela tela: captura (mss) + comparação de retratos de heróis (OpenCV).

Só lê pixels da tela, como uma gravação faria: não lê memória do jogo, não injeta overlay e
não envia comandos. A posição dos slots muda com resolução e HUD, por isso ela é calibrada
uma vez por máquina (`python -m draft_assistant calibrate`) e salva em calibration.json.
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from .data_sources import STEAM_CDN, USER_AGENT
from .models import DraftState, Hero


def _cv2():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise SystemExit("Instale as dependências de tela: pip install opencv-python mss numpy") from exc
    return cv2, np


@dataclass
class Slot:
    # Coordenadas relativas (0..1) à captura, assim a calibração sobrevive a trocas de resolução.
    x: float
    y: float
    w: float
    h: float

    def crop(self, image):
        height, width = image.shape[:2]
        x0, y0 = int(self.x * width), int(self.y * height)
        x1, y1 = int((self.x + self.w) * width), int((self.y + self.h) * height)
        return image[y0:y1, x0:x1]


@dataclass
class Calibration:
    monitor: int = 1
    my_side: str = "radiant"
    radiant_picks: list[Slot] = field(default_factory=list)
    dire_picks: list[Slot] = field(default_factory=list)
    bans: list[Slot] = field(default_factory=list)
    threshold: float = 0.55

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        for key in ("radiant_picks", "dire_picks", "bans"):
            raw[key] = [Slot(**s) for s in raw.get(key, [])]
        return cls(**raw)


def download_templates(heroes: dict[int, Hero], directory: Path, session: requests.Session | None = None) -> int:
    """Baixa os retratos oficiais do CDN da Steam (caminhos vindos do /heroStats)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    count = 0
    for hero in heroes.values():
        target = directory / f"{hero.id}_{hero.short_name}.png"
        if target.exists() or not hero.img:
            continue
        resp = session.get(STEAM_CDN + hero.img.split("?")[0], timeout=30)
        resp.raise_for_status()
        target.write_bytes(resp.content)
        count += 1
    return count


class TemplateMatcher:
    """Compara cada slot com todos os retratos; aceita o melhor acima do limiar."""

    def __init__(self, templates: dict[int, list], threshold: float = 0.55):
        self.cv2, self.np = _cv2()
        self.templates = templates
        self.threshold = threshold

    @classmethod
    def from_directory(cls, directory: Path, threshold: float = 0.55) -> "TemplateMatcher":
        cv2, _ = _cv2()
        templates: dict[int, list] = {}
        # Vários arquivos por herói são aceitos (ex.: "12_phantom_lancer_topbar.png" recortado da sua tela).
        for path in sorted(Path(directory).glob("*.png")):
            prefix = path.stem.split("_", 1)[0]
            if not prefix.isdigit():
                continue  # ex.: recortes "crop_..." ainda não rotulados
            hero_id = int(prefix)
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is not None:
                templates.setdefault(hero_id, []).append(image)
        return cls(templates, threshold)

    def match(self, crop) -> tuple[int | None, float]:
        cv2 = self.cv2
        if crop.size == 0:
            return None, 0.0
        crop_h, crop_w = crop.shape[:2]
        best_id, best = None, -1.0
        for hero_id, images in self.templates.items():
            for image in images:
                # O retrato ocupa ~90% do slot; deslizar cobre pequenos erros de calibração.
                scale = min(crop_w / image.shape[1], crop_h / image.shape[0]) * 0.9
                size = (max(1, int(image.shape[1] * scale)), max(1, int(image.shape[0] * scale)))
                resized = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
                value = float(cv2.matchTemplate(crop, resized, cv2.TM_CCOEFF_NORMED).max())
                if value > best:
                    best_id, best = hero_id, value
        return (best_id, best) if best >= self.threshold else (None, best)


def read_draft(image, calibration: Calibration, matcher: TemplateMatcher) -> DraftState:
    def read(slots):
        found = []
        for slot in slots:
            hero_id, _ = matcher.match(slot.crop(image))
            if hero_id is not None and hero_id not in found:
                found.append(hero_id)
        return found

    radiant, dire = read(calibration.radiant_picks), read(calibration.dire_picks)
    mine, theirs = (radiant, dire) if calibration.my_side == "radiant" else (dire, radiant)
    return DraftState(allies=mine, enemies=theirs, bans=read(calibration.bans))


def grab(monitor: int = 1):
    import mss

    _, np = _cv2()
    with mss.mss() as sct:
        shot = np.array(sct.grab(sct.monitors[monitor]))
    return shot[:, :, :3].copy()  # BGRA -> BGR


def watch(calibration: Calibration, matcher: TemplateMatcher, on_draft, interval: float = 1.0) -> None:
    """Lê a tela a cada `interval` s; só avisa quando duas leituras seguidas concordam."""
    last_reported, pending = None, None
    while True:
        state = read_draft(grab(calibration.monitor), calibration, matcher)
        if state.key() == pending and state.key() != last_reported:
            last_reported = state.key()
            on_draft(state)
        pending = state.key()
        time.sleep(interval)


def calibrate(path: Path, monitor: int = 1, my_side: str = "radiant") -> Calibration:
    """Abre uma captura da tela de draft e pede para marcar os slots com o mouse."""
    cv2, _ = _cv2()
    image = grab(monitor)
    height, width = image.shape[:2]
    calibration = Calibration(monitor=monitor, my_side=my_side)
    for key, label in (("radiant_picks", "5 picks Radiant (esq. -> dir.)"),
                       ("dire_picks", "5 picks Dire (esq. -> dir.)"),
                       ("bans", "slots de ban (todos)")):
        print(f"Marque {label}; ENTER confirma cada caixa, ESC termina.")
        boxes = cv2.selectROIs(label, image, showCrosshair=False)
        cv2.destroyWindow(label)
        setattr(calibration, key, [Slot(x / width, y / height, w / width, h / height) for x, y, w, h in boxes])
    calibration.save(path)
    return calibration


def save_crops(calibration: Calibration, directory: Path, monitor: int | None = None) -> list[Path]:
    """Salva o recorte de cada slot; renomeie para "<hero_id>_<nome>.png" e use como template extra."""
    cv2, _ = _cv2()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image = grab(monitor or calibration.monitor)
    stamp = int(time.time())
    paths = []
    for key in ("radiant_picks", "dire_picks", "bans"):
        for i, slot in enumerate(getattr(calibration, key)):
            path = directory / f"crop_{stamp}_{key}_{i}.png"
            cv2.imwrite(str(path), slot.crop(image))
            paths.append(path)
    return paths
