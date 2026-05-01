from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from homestretch_data.models import (
    Form,
    FormCategory,
    Game,
    Method,
    Platform,
    Source,
    Transfer,
    TransferMechanism,
    TransferRoute,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


def _load(name: str) -> list[dict]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def test_game_round_trip() -> None:
    g = Game(
        id="scarlet",
        name="Pokémon Scarlet",
        generation=9,
        platform=Platform.NINTENDO_SWITCH,
        release_year=2022,
        home_compatible=True,
        transfer_route=TransferRoute.DIRECT,
    )
    assert Game.model_validate(g.model_dump()) == g


def test_game_id_pattern_rejects_uppercase() -> None:
    with pytest.raises(ValidationError):
        Game(
            id="Scarlet",
            name="x",
            platform=Platform.NINTENDO_SWITCH,
            release_year=2022,
            home_compatible=True,
            transfer_route=TransferRoute.DIRECT,
        )


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        Game.model_validate(
            {
                "id": "scarlet",
                "name": "Pokémon Scarlet",
                "platform": "nintendo-switch",
                "release_year": 2022,
                "home_compatible": True,
                "transfer_route": "direct",
                "bogus": "x",
            }
        )


def test_form_requires_valid_generation() -> None:
    with pytest.raises(ValidationError):
        Form(
            id="vulpix-alolan",
            species_id="vulpix",
            national_dex=37,
            generation_introduced=99,
            categories=[FormCategory.REGIONAL_VARIANT],
            sprite_url="https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/10103.png",
        )


def test_source_enum_serialization() -> None:
    s = Source(
        form_id="vulpix-alolan",
        game_id="sun",
        method=Method.WILD_ENCOUNTER,
    )
    assert s.model_dump()["method"] == "wild-encounter"


def test_transfer_enum_serialization() -> None:
    t = Transfer(
        from_id="scarlet",
        to_id="home",
        mechanism=TransferMechanism.POKEMON_HOME,
        bidirectional=True,
    )
    assert t.model_dump()["mechanism"] == "home"


def test_games_json_validates() -> None:
    TypeAdapter(list[Game]).validate_python(_load("games.json"))


def test_transfers_json_validates() -> None:
    TypeAdapter(list[Transfer]).validate_python(_load("transfers.json"))


def test_forms_json_validates() -> None:
    TypeAdapter(list[Form]).validate_python(_load("forms.json"))


def test_sources_json_validates() -> None:
    TypeAdapter(list[Source]).validate_python(_load("sources.json"))
