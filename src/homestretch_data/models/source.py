from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from homestretch_data.models.form import FormId
from homestretch_data.models.game import GameId


class Method(StrEnum):
    """Broad acquisition method. Specifics go in `method_details`."""

    WILD_ENCOUNTER = "wild-encounter"
    STATIC_ENCOUNTER = "static-encounter"
    GIFT = "gift"
    EVOLUTION = "evolution"
    BREEDING = "breeding"
    TRADE = "trade"
    FOSSIL_REVIVE = "fossil-revive"
    PURCHASE = "purchase"
    FISHING = "fishing"
    RAID = "raid"
    EVENT = "event"
    TRANSFER = "transfer"


class Source(BaseModel):
    """A way to obtain a particular form inside a particular game."""

    model_config = ConfigDict(extra="forbid")

    form_id: FormId
    game_id: GameId
    method: Method
    method_details: str | None = None
    requires_dlc: str | None = None
    requires_trade: bool = False
    notes: str | None = None
