from __future__ import annotations

import hashlib
import random
import string
from dataclasses import dataclass, field
from string import Formatter
from typing import Any, Protocol

from ..contracts import Choice, QuestionnaireJson, RecipientRow
from ..validation import validate_questionnaire_json


@dataclass(frozen=True)
class QuestionUnit:
    """
    One 'question' worth of content: vignette + a single singleSelect decision question.
    Later we can add other types, but this is the MVP unit.
    """

    vignette_text: str
    prompt: str
    choices: list[Choice]
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PickerContext:
    campaign_key: str
    questionnaire_version: int
    title: str
    seed: int
    k: int


class QPicker(Protocol):
    """
    Strategy interface for generating QuestionUnits per recipient.
    """

    def generate_units(self, *, recipient: RecipientRow, context: PickerContext) -> list[QuestionUnit]:
        ...


def stable_int_seed(*parts: Any) -> int:
    """
    Create a stable integer seed from arbitrary values.
    """
    s = "|".join(str(p) for p in parts)
    h = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big", signed=False)


def build_questionnaire_from_units(
    *,
    title: str,
    questionnaire_version: int,
    units: list[QuestionUnit],
) -> QuestionnaireJson:
    blocks: list[dict[str, Any]] = []
    for idx, u in enumerate(units, start=1):
        blocks.append({"type": "vignette", "id": f"vignette_{idx}", "text": u.vignette_text})
        blocks.append(
            {
                "type": "singleSelect",
                "id": f"decision_{idx}",
                "prompt": u.prompt,
                "required": True,
                "choices": u.choices,
            }
        )

    q: dict[str, Any] = {"title": title, "questionnaireVersion": questionnaire_version, "blocks": blocks}
    return validate_questionnaire_json(q)


class StrictFormatDict(dict):
    """
    Used with str.format_map(). Raises KeyError for missing fields with a clear error.
    """

    def __missing__(self, key: str) -> str:  # pragma: no cover
        raise KeyError(key)


def required_placeholders(fmt: str) -> set[str]:
    out: set[str] = set()
    for literal, field_name, fmt_spec, conv in Formatter().parse(fmt):
        if field_name:
            # field_name can include indexing, attributes etc. Keep MVP to simple identifiers.
            out.add(field_name)
    return out


def validate_placeholder_names(names: set[str]) -> None:
    allowed = set(string.ascii_letters + string.digits + "_")
    for n in names:
        if any(ch not in allowed for ch in n):
            raise ValueError(f"Unsupported placeholder name '{n}'. Use only [A-Za-z0-9_].")


def seeded_rng(*seed_parts: Any) -> random.Random:
    return random.Random(stable_int_seed(*seed_parts))


