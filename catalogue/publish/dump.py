"""Serialise a published document after checking it against its model."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from catalogue.errors import PipelineError


def dump_document(model: type[BaseModel], document: dict[str, Any], name: str) -> bytes:
    try:
        model.model_validate(document)
    except ValidationError:
        raise PipelineError(f"{name} does not match its schema") from None
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")
