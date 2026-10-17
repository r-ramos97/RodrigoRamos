from dataclasses import dataclass, field


@dataclass(frozen=True)
class Hero:
    id: int
    name: str  # "npc_dota_hero_antimage"
    localized_name: str  # "Anti-Mage"
    roles: tuple[str, ...] = ()
    img: str | None = None  # caminho no CDN da Steam, vindo do /heroStats

    @property
    def short_name(self) -> str:
        return self.name.removeprefix("npc_dota_hero_")


@dataclass
class DraftState:
    allies: list[int] = field(default_factory=list)
    enemies: list[int] = field(default_factory=list)
    bans: list[int] = field(default_factory=list)

    @property
    def unavailable(self) -> set[int]:
        return set(self.allies) | set(self.enemies) | set(self.bans)

    def key(self) -> tuple:
        return (tuple(sorted(self.allies)), tuple(sorted(self.enemies)), tuple(sorted(self.bans)))
