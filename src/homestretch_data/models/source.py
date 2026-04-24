from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


TimeOfDay = Literal["day", "night", "dusk", "full-moon"]
Gender = Literal["male", "female"]
RelativeStats = Literal["atk-gt-def", "atk-lt-def", "atk-eq-def"]


class Source(BaseModel):
    """A way to obtain a particular form inside a particular game.

    `method` is the broad category. `method_details` is a short categorical
    slug that refines the subtype (e.g. `walk` / `surf` / `tera-raid` /
    `level-up` / `use-item`). Specific entities — items held or used,
    evolution locations, trade/party partner species, move conditions — go
    in the structured fields below. This keeps `method_details` queryable
    as a facet and the specifics available without string parsing.
    """

    model_config = ConfigDict(extra="forbid")

    form_id: FormId
    game_id: GameId
    method: Method
    method_details: str | None = None
    requires_dlc: str | None = None
    requires_trade: bool = False
    notes: str | None = None

    # Structured conditions. All optional — most rows set none of these.
    # String fields are lowercase-slug form; no FK tables yet.
    item: str | None = None
    """Stone/item consumed (use-item trigger) or required hold during
    level-up (e.g. razor-fang → gliscor). Lowercase slug from PokéAPI's
    item.name (e.g. `ice-stone`, `black-augurite`)."""

    held_item: str | None = None
    """Item that must be held during the evolution trigger. Canonical
    example: `metal-coat` for Scizor, `kings-rock` for Politoed/Slowking.
    Distinct from `item` because PokéAPI splits them; both can be set in
    rare cases."""

    location: str | None = None
    """Acquisition location slug (e.g. `mount-lanakila`, `ambrette-town`,
    `sinnoh-route-217`). Populated for evolution rows (PokéAPI's
    `location.name`) and for non-wild rows whose spot is determined by
    game + method (fossil-revive, breeding, game-corner purchase)."""

    known_move: str | None = None
    """Move slug that must be known. PokéAPI's known_move.name."""

    known_move_type: str | None = None
    """Type slug for 'must know a move of this type' evolutions (Sylveon
    requires a Fairy move). PokéAPI's known_move_type.name."""

    trade_species: FormId | None = None
    """Species that must be traded for (Shelmet ↔ Karrablast). Uses the
    default form id of the trade partner."""

    party_species: FormId | None = None
    """Species that must be in the party (Mantyke → Mantine requires
    Remoraid). Default form id."""

    party_type: str | None = None
    """Type required in party (e.g. `dark` for Pangoro). PokéAPI's
    party_type.name."""

    from_form: FormId | None = None
    """Provenance: the specific pre-evolution form or breeding parent this
    row is derived from. Used to disambiguate regional-variant evolutions
    (runerigus from yamask-galar, sirfetch'd from farfetchd-galar) and
    breeding rows (pichu from pikachu)."""

    time_of_day: TimeOfDay | None = None
    """`day` / `night` / `dusk` requirement."""

    min_happiness: int | None = Field(default=None, ge=0)
    """Minimum friendship threshold (PokéAPI uses 160 as the canonical
    high-friendship value)."""

    min_affection: int | None = Field(default=None, ge=0)
    """Minimum affection hearts (Sylveon in Gen 6-7)."""

    min_beauty: int | None = Field(default=None, ge=0)
    """Minimum beauty stat (Milotic via Feebas in Gen 3-5)."""

    gender: Gender | None = None
    """Only this gender can evolve this way (e.g. Kirlia → Gallade is
    male-only)."""

    relative_physical_stats: RelativeStats | None = None
    """Tyrogue → Hitmonlee/Hitmonchan/Hitmontop branch based on the
    relationship between Attack and Defense."""

    needs_overworld_rain: bool = False
    """Must be raining in the overworld (Goomy → Sliggoo in Gen 6+)."""

    turn_upside_down: bool = False
    """Console must be held upside-down (Inkay → Malamar in Gen 6-7)."""

    needs_multiplayer: bool = False
    """Requires a second player nearby with the pre-evolution in their
    party (Finizen → Palafin in Scarlet/Violet)."""
