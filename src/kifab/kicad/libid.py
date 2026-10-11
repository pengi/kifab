"""Library identifiers (``nickname:item``) as used by symbols and footprints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class LibId:
    """``"jlcpcb:R0603"`` -> ``LibId("jlcpcb", "R0603")``. ``nickname`` may be empty."""

    nickname: str
    name: str

    @classmethod
    def parse(cls, text: str) -> LibId:
        nickname, sep, name = text.partition(":")
        if not sep:
            return cls("", text)
        return cls(nickname, name)

    def __str__(self) -> str:
        return f"{self.nickname}:{self.name}" if self.nickname else self.name
