from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

GameId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=40),
]


class Platform(StrEnum):
    GAME_BOY = "game-boy"
    GAME_BOY_COLOR = "game-boy-color"
    GAME_BOY_ADVANCE = "game-boy-advance"
    NINTENDO_DS = "nintendo-ds"
    NINTENDO_3DS = "nintendo-3ds"
    WII = "wii"
    WII_U = "wii-u"
    NINTENDO_SWITCH = "nintendo-switch"
    NINTENDO_SWITCH_2 = "nintendo-switch-2"
    MOBILE = "mobile"
    SERVICE = "service"  # Pokémon HOME, Bank, etc.


class TransferRoute(StrEnum):
    """How a game's Pokémon reach Pokémon HOME."""

    DIRECT = "direct"
    VIA_BANK = "via-bank"
    VIA_TRANSPORTER = "via-transporter"
    VIA_GO_LINK = "via-go-link"
    NONE = "none"


class Game(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: GameId
    name: str = Field(min_length=1)
    generation: int | None = Field(default=None, ge=1, le=9)
    platform: Platform
    release_year: int = Field(ge=1996, le=2100)
    home_compatible: bool
    transfer_route: TransferRoute
    is_service: bool = False
    notes: str | None = None
