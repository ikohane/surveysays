from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict


# -------------------------
# Questionnaire JSON contract (MVP)
# -------------------------


BlockType = Literal["vignette", "singleSelect", "freeText"]


class Choice(TypedDict):
    id: str
    label: str


class VignetteBlock(TypedDict):
    type: Literal["vignette"]
    id: str
    text: str


class SingleSelectQuestion(TypedDict):
    type: Literal["singleSelect"]
    id: str
    prompt: str
    required: bool
    choices: list[Choice]


class FreeTextQuestion(TypedDict):
    """
    MVP: required, single-line free text response.
    (No constraints yet: no min/max length, no regex, no multiline flag.)
    """

    type: Literal["freeText"]
    id: str
    prompt: str
    required: bool


QuestionnaireJson = TypedDict(
    "QuestionnaireJson",
    {
        "title": str,
        "questionnaireVersion": int,
        "blocks": list[VignetteBlock | SingleSelectQuestion | FreeTextQuestion],
    },
)


# -------------------------
# Bulk invitations export contract
# -------------------------


class BulkInvitation(TypedDict, total=False):
    email: str
    questionnaireVersion: int
    questionnaireJson: QuestionnaireJson
    metadata: dict[str, Any]


class BulkInvitationsPayload(TypedDict):
    campaignKey: str
    invitations: list[BulkInvitation]


# -------------------------
# CSV input contracts (local Admin app imports)
# -------------------------


@dataclass(frozen=True)
class CaseRow:
    case_id: str
    vignette: str
    prompt: str
    choices: list[Choice]
    tags: list[str]


@dataclass(frozen=True)
class RecipientRow:
    email: str
    strata: dict[str, str]


