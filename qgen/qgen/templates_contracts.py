from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml

from .contracts import Choice


class TemplatesContractError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateRow:
    template_id: str
    vignette_template: str
    prompt_template: str
    choices: list[Choice]
    tags: list[str]
    rules: dict[str, Any]


def _split_tags(tags: str) -> list[str]:
    tags = (tags or "").strip()
    if not tags:
        return []
    return [t.strip() for t in tags.split("|") if t.strip()]


def _get_required(row: dict[str, str], key: str, *, row_name: str) -> str:
    v = (row.get(key) or "").strip()
    if not v:
        raise TemplatesContractError(f"{row_name}: missing required column '{key}' (or empty value)")
    return v


def parse_template_row(row: dict[str, str], *, row_name: str) -> TemplateRow:
    """
    Required columns:
      - template_id
      - vignette_template
      - prompt_template

    Choices: one of:
      - choices_json: JSON array like [{\"id\":\"A\",\"label\":\"...\"}, ...]
      - OR choice_* columns (e.g. choice_A, choice_B...), where suffix becomes choice id.

    Rules:
      - rules_yaml: inline YAML text (optional; default empty)
    """
    template_id = _get_required(row, "template_id", row_name=row_name)
    vignette_template = _get_required(row, "vignette_template", row_name=row_name)
    prompt_template = _get_required(row, "prompt_template", row_name=row_name)
    tags = _split_tags(row.get("tags") or "")

    choices: list[Choice] = []
    choices_json = (row.get("choices_json") or "").strip()
    if choices_json:
        try:
            parsed = json.loads(choices_json)
        except json.JSONDecodeError as e:
            raise TemplatesContractError(f"{row_name}: choices_json is not valid JSON: {e}") from e
        if not isinstance(parsed, list) or len(parsed) < 2:
            raise TemplatesContractError(f"{row_name}: choices_json must be a JSON array with >= 2 items")
        for i, c in enumerate(parsed):
            if not isinstance(c, dict):
                raise TemplatesContractError(f"{row_name}: choices_json[{i}] must be an object")
            cid = (c.get("id") or "").strip()
            label = (c.get("label") or "").strip()
            if not cid or not label:
                raise TemplatesContractError(f"{row_name}: each choice must have non-empty id and label")
            choices.append({"id": cid, "label": label})
    else:
        choice_cols = [(k, v) for k, v in row.items() if k.startswith("choice_")]
        for k, v in sorted(choice_cols, key=lambda kv: kv[0]):
            label = (v or "").strip()
            if not label:
                continue
            cid = k.removeprefix("choice_").strip()
            if not cid:
                raise TemplatesContractError(f"{row_name}: invalid choice column '{k}' (missing suffix id)")
            choices.append({"id": cid, "label": label})

    if len(choices) < 2:
        raise TemplatesContractError(
            f"{row_name}: need at least 2 choices (provide choices_json or at least two choice_* columns)"
        )

    ids = [c["id"] for c in choices]
    if len(set(ids)) != len(ids):
        raise TemplatesContractError(f"{row_name}: duplicate choice ids in choices")

    rules_yaml = (row.get("rules_yaml") or "").strip()
    # When YAML is embedded in a single CSV cell, newlines are often represented as literal '\n'.
    # Normalize those so YAML parsing behaves as expected.
    rules_yaml = rules_yaml.replace("\\r\\n", "\n").replace("\\n", "\n")
    if rules_yaml:
        try:
            rules = yaml.safe_load(rules_yaml)
        except Exception as e:
            raise TemplatesContractError(f"{row_name}: rules_yaml is not valid YAML: {e}") from e
        if rules is None:
            rules = {}
        if not isinstance(rules, dict):
            raise TemplatesContractError(f"{row_name}: rules_yaml must parse to a mapping/object")
    else:
        rules = {}

    return TemplateRow(
        template_id=template_id,
        vignette_template=vignette_template,
        prompt_template=prompt_template,
        choices=choices,
        tags=tags,
        rules=rules,
    )


def parse_param_vector_json(obj: Any) -> dict[str, Any]:
    """
    MVP param vector schema:
      {
        \"pools\": {
          \"poolName\": [\"v1\", \"v2\", ...] | [1,2,3] | ...
        }
      }
    """
    if not isinstance(obj, dict):
        raise TemplatesContractError("param_vector.json must be an object")
    pools = obj.get("pools")
    if pools is None:
        raise TemplatesContractError("param_vector.json must include 'pools'")
    if not isinstance(pools, dict):
        raise TemplatesContractError("param_vector.json.pools must be an object")
    for k, v in pools.items():
        if not isinstance(k, str) or not k.strip():
            raise TemplatesContractError("param_vector.json.pools keys must be non-empty strings")
        if not isinstance(v, list) or len(v) == 0:
            raise TemplatesContractError(f"param_vector.json.pools.{k} must be a non-empty array")
    return obj


