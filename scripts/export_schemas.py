"""Export a JSON Schema file for each Pydantic model into schemas/.

Run via: `uv run python scripts/export_schemas.py`
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, RootModel

from homestretch_data.models import Form, Game, Source, Transfer

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


def _dump_schema(model: type[BaseModel], filename: str) -> None:
    schema = model.model_json_schema()
    path = SCHEMAS_DIR / filename
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def main() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)

    _dump_schema(Game, "game.schema.json")
    _dump_schema(Form, "form.schema.json")
    _dump_schema(Source, "source.schema.json")
    _dump_schema(Transfer, "transfer.schema.json")

    # Array wrappers so data files (which are JSON arrays) can be validated directly.
    class GameList(RootModel[list[Game]]):
        pass

    class FormList(RootModel[list[Form]]):
        pass

    class SourceList(RootModel[list[Source]]):
        pass

    class TransferList(RootModel[list[Transfer]]):
        pass

    _dump_schema(GameList, "games.schema.json")
    _dump_schema(FormList, "forms.schema.json")
    _dump_schema(SourceList, "sources.schema.json")
    _dump_schema(TransferList, "transfers.schema.json")


if __name__ == "__main__":
    main()
