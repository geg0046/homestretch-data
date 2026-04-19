from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NodeId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=40),
]


class TransferMechanism(StrEnum):
    POKEMON_HOME = "pokemon-home"
    POKEMON_BANK = "pokemon-bank"
    POKE_TRANSPORTER = "poke-transporter"
    GO_LINK = "go-link"
    LETS_GO_LINK = "lets-go-link"
    DIRECT_TRADE = "direct-trade"


class Transfer(BaseModel):
    """Directed edge between games/services describing how mons can move."""

    model_config = ConfigDict(extra="forbid")

    from_id: NodeId
    to_id: NodeId
    mechanism: TransferMechanism
    bidirectional: bool = False
    notes: str | None = None
