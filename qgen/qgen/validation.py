from __future__ import annotations

from typing import Any

from .contracts import QuestionnaireJson


class ValidationError(ValueError):
    pass


def _is_nonempty_str(x: Any) -> bool:
    return isinstance(x, str) and x.strip() != ""


def validate_questionnaire_json(obj: Any) -> QuestionnaireJson:
    """
    Minimal, strict-ish validator for the MVP Questionnaire JSON contract.
    Returns the object typed as QuestionnaireJson on success.
    Raises ValidationError with a helpful message on failure.
    """
    if not isinstance(obj, dict):
        raise ValidationError("questionnaireJson must be an object")

    title = obj.get("title")
    if not _is_nonempty_str(title):
        raise ValidationError("questionnaireJson.title must be a non-empty string")

    version = obj.get("questionnaireVersion")
    if not isinstance(version, int) or version < 1:
        raise ValidationError("questionnaireJson.questionnaireVersion must be an integer >= 1")

    blocks = obj.get("blocks")
    if not isinstance(blocks, list) or len(blocks) == 0:
        raise ValidationError("questionnaireJson.blocks must be a non-empty array")

    seen_ids: set[str] = set()
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            raise ValidationError(f"questionnaireJson.blocks[{i}] must be an object")
        btype = b.get("type")
        if btype not in ("vignette", "singleSelect", "freeText"):
            raise ValidationError(
                f"questionnaireJson.blocks[{i}].type must be 'vignette', 'singleSelect', or 'freeText'"
            )
        bid = b.get("id")
        if not _is_nonempty_str(bid):
            raise ValidationError(f"questionnaireJson.blocks[{i}].id must be a non-empty string")
        if bid in seen_ids:
            raise ValidationError(f"Duplicate block/question id: '{bid}'")
        seen_ids.add(bid)

        if btype == "vignette":
            text = b.get("text")
            if not _is_nonempty_str(text):
                raise ValidationError(f"questionnaireJson.blocks[{i}].text must be a non-empty string")
        elif btype == "singleSelect":
            prompt = b.get("prompt")
            if not _is_nonempty_str(prompt):
                raise ValidationError(f"questionnaireJson.blocks[{i}].prompt must be a non-empty string")
            required = b.get("required")
            if required is not True:
                raise ValidationError(f"questionnaireJson.blocks[{i}].required must be true for MVP")
            choices = b.get("choices")
            if not isinstance(choices, list) or len(choices) < 2:
                raise ValidationError(f"questionnaireJson.blocks[{i}].choices must be an array with >= 2 items")
            seen_choice_ids: set[str] = set()
            for j, c in enumerate(choices):
                if not isinstance(c, dict):
                    raise ValidationError(f"questionnaireJson.blocks[{i}].choices[{j}] must be an object")
                cid = c.get("id")
                if not _is_nonempty_str(cid):
                    raise ValidationError(f"questionnaireJson.blocks[{i}].choices[{j}].id must be non-empty")
                if cid in seen_choice_ids:
                    raise ValidationError(
                        f"questionnaireJson.blocks[{i}].choices has duplicate id '{cid}'"
                    )
                seen_choice_ids.add(cid)
                label = c.get("label")
                if not _is_nonempty_str(label):
                    raise ValidationError(f"questionnaireJson.blocks[{i}].choices[{j}].label must be non-empty")
        else:
            # freeText (MVP): required, single-line.
            prompt = b.get("prompt")
            if not _is_nonempty_str(prompt):
                raise ValidationError(f"questionnaireJson.blocks[{i}].prompt must be a non-empty string")
            required = b.get("required")
            if required is not True:
                raise ValidationError(f"questionnaireJson.blocks[{i}].required must be true for MVP")

    return obj  # type: ignore[return-value]


