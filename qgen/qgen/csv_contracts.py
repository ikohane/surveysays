from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .contracts import CaseRow, Choice, RecipientRow


class CsvContractError(ValueError):
    pass


def _get_required(row: dict[str, str], key: str, *, row_name: str) -> str:
    v = (row.get(key) or "").strip()
    if not v:
        raise CsvContractError(f"{row_name}: missing required column '{key}' (or empty value)")
    return v


def _split_tags(tags: str) -> list[str]:
    tags = tags.strip()
    if not tags:
        return []
    return [t.strip() for t in tags.split("|") if t.strip()]


def parse_case_row(row: dict[str, str], *, row_name: str) -> CaseRow:
    """
    Required columns:
      - case_id
      - vignette
      - prompt

    Choices: one of:
      - choices_json: JSON array like [{\"id\":\"A\",\"label\":\"...\"}, ...]
      - OR choice_* columns (e.g. choice_A, choice_B, choice_C...), where suffix becomes choice id.

    Optional:
      - tags: pipe-separated list (e.g. cardio|adult)
    """
    case_id = _get_required(row, "case_id", row_name=row_name)
    vignette = _get_required(row, "vignette", row_name=row_name)
    prompt = _get_required(row, "prompt", row_name=row_name)
    tags = _split_tags((row.get("tags") or ""))

    choices: list[Choice] = []

    choices_json = (row.get("choices_json") or "").strip()
    if choices_json:
        try:
            parsed = json.loads(choices_json)
        except json.JSONDecodeError as e:
            raise CsvContractError(f"{row_name}: choices_json is not valid JSON: {e}") from e
        if not isinstance(parsed, list) or len(parsed) < 2:
            raise CsvContractError(f"{row_name}: choices_json must be a JSON array with >= 2 items")
        for i, c in enumerate(parsed):
            if not isinstance(c, dict):
                raise CsvContractError(f"{row_name}: choices_json[{i}] must be an object")
            cid = (c.get("id") or "").strip()
            label = (c.get("label") or "").strip()
            if not cid or not label:
                raise CsvContractError(f"{row_name}: each choice must have non-empty id and label")
            choices.append({"id": cid, "label": label})
    else:
        # Collect choice_* columns
        choice_cols = [(k, v) for k, v in row.items() if k.startswith("choice_")]
        for k, v in sorted(choice_cols, key=lambda kv: kv[0]):
            label = (v or "").strip()
            if not label:
                continue
            cid = k.removeprefix("choice_").strip()
            if not cid:
                raise CsvContractError(f"{row_name}: invalid choice column '{k}' (missing suffix id)")
            choices.append({"id": cid, "label": label})

    if len(choices) < 2:
        raise CsvContractError(
            f"{row_name}: need at least 2 choices (provide choices_json or at least two choice_* columns)"
        )

    # Ensure unique choice IDs
    ids = [c["id"] for c in choices]
    if len(set(ids)) != len(ids):
        raise CsvContractError(f"{row_name}: duplicate choice ids in choices")

    return CaseRow(case_id=case_id, vignette=vignette, prompt=prompt, choices=choices, tags=tags)


def parse_recipient_row(row: dict[str, str], *, row_name: str) -> RecipientRow:
    """
    Required columns:
      - email

    All other columns are treated as strata/metadata (strings).
    """
    email = _get_required(row, "email", row_name=row_name).lower()
    strata = {k: (v or "").strip() for k, v in row.items() if k != "email" and (v or "").strip() != ""}
    return RecipientRow(email=email, strata=strata)


def case_to_jsonable(case: CaseRow) -> dict[str, Any]:
    return asdict(case)


def recipient_to_jsonable(rec: RecipientRow) -> dict[str, Any]:
    return asdict(rec)


