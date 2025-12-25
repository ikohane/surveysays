from __future__ import annotations

from typing import Any, Literal

from .contracts import BulkInvitation, BulkInvitationsPayload, CaseRow, RecipientRow
from .hashing import questionnaire_hash
from .io_templates_csv import parse_templates_csv
from .qpicker.base import PickerContext, build_questionnaire_from_units
from .qpicker.pick_k_cases import PickKCasesPicker
from .qpicker.template_expand import TemplateExpandPicker
from .templates_contracts import parse_param_vector_json


PickerStrategy = Literal["pick_k_cases", "template_expand"]


def generate_bulk_payload(
    *,
    campaign_key: str,
    title: str,
    questionnaire_version: int,
    cases: list[CaseRow],
    recipients: list[RecipientRow],
    seed: int,
    picker_strategy: PickerStrategy = "pick_k_cases",
    k: int = 1,
    templates_csv_text: str | None = None,
    param_vector_obj: dict[str, Any] | None = None,
) -> BulkInvitationsPayload:
    context = PickerContext(
        campaign_key=campaign_key,
        questionnaire_version=questionnaire_version,
        title=title,
        seed=seed,
        k=k,
    )

    if picker_strategy == "pick_k_cases":
        picker = PickKCasesPicker(cases=cases)
    elif picker_strategy == "template_expand":
        if not templates_csv_text:
            raise ValueError("templates_csv_text is required for template_expand")
        if param_vector_obj is None:
            raise ValueError("param_vector_obj is required for template_expand")
        templates = parse_templates_csv(templates_csv_text)
        param_vector = parse_param_vector_json(param_vector_obj)
        picker = TemplateExpandPicker(templates=templates, param_vector=param_vector)
    else:
        raise ValueError(f"Unsupported picker_strategy: {picker_strategy}")

    invitations: list[BulkInvitation] = []
    for r in sorted(recipients, key=lambda x: x.email):
        units = picker.generate_units(recipient=r, context=context)
        questionnaire_json = build_questionnaire_from_units(
            title=title,
            questionnaire_version=questionnaire_version,
            units=units,
        )
        meta: dict[str, Any] = {
            "campaignKey": campaign_key,
            "recipientStrata": r.strata,
            "seed": seed,
            "k": k,
            "pickerStrategy": picker_strategy,
        }
        # include per-unit metadata in a compact list for later debugging
        meta["units"] = [u.metadata for u in units]
        meta["questionnaireHash"] = questionnaire_hash(questionnaire_json)

        invitations.append(
            {
                "email": r.email,
                "questionnaireVersion": questionnaire_version,
                "questionnaireJson": questionnaire_json,
                "metadata": meta,
            }
        )

    return {"campaignKey": campaign_key, "invitations": invitations}


