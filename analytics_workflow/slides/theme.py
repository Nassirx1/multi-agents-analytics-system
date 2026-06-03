from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConsultingTheme:
    name: str = "consulting_minimal"
    width: float = 13.333
    height: float = 7.5
    margin_x: float = 0.68
    top_y: float = 0.36
    content_top: float = 1.35
    footer_y: float = 7.05
    font_family: str = "Calibri"
    background: tuple[int, int, int] = (247, 248, 246)
    panel: tuple[int, int, int] = (255, 255, 255)
    ink: tuple[int, int, int] = (31, 39, 48)
    muted: tuple[int, int, int] = (94, 105, 116)
    navy: tuple[int, int, int] = (21, 48, 76)
    blue: tuple[int, int, int] = (44, 101, 164)
    teal: tuple[int, int, int] = (53, 128, 127)
    green: tuple[int, int, int] = (73, 132, 92)
    gold: tuple[int, int, int] = (187, 146, 65)
    red: tuple[int, int, int] = (168, 83, 76)
    line: tuple[int, int, int] = (209, 215, 220)
    pale_blue: tuple[int, int, int] = (232, 239, 247)
    pale_gold: tuple[int, int, int] = (247, 239, 220)
    white: tuple[int, int, int] = (255, 255, 255)

    @property
    def chart_palette(self) -> list[tuple[int, int, int]]:
        return [self.blue, self.teal, self.gold, self.green, self.red]


THEME = ConsultingTheme()
