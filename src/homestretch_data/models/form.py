from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FormId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=80),
]
SpeciesId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=2, max_length=40),
]


class FormCategory(StrEnum):
    REGIONAL_VARIANT = "regional-variant"
    MEGA = "mega"
    PRIMAL = "primal"
    GIGANTAMAX = "gigantamax"
    GENDER_DIFFERENCE = "gender-difference"
    EVENT_ONLY = "event-only"
    COSMETIC = "cosmetic"
    FUNCTIONAL = "functional"
    TOTEM = "totem"


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: FormId
    species_id: SpeciesId
    national_dex: int = Field(ge=1, le=2000)
    form_name: str | None = None
    is_default: bool = False
    generation_introduced: int = Field(ge=1, le=9)
    categories: list[FormCategory] = Field(default_factory=list)
    notes: str | None = None
