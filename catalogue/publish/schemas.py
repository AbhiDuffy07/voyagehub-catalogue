"""Write the JSON Schemas of the published files to `schemas/`.

Run: python -m catalogue.publish.schemas.

The app validates against these; a test fails when the committed files differ from the models.
"""

import json
from pathlib import Path

from catalogue.publish.models import SCHEMAS

SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"


def export_schemas(target: Path = SCHEMAS_DIR) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        text = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        (target / f"{name}.schema.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    export_schemas()
