from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts import Choice, RecipientRow
from ..io_templates_csv import parse_templates_csv
from ..templates_contracts import TemplateRow, parse_param_vector_json
from .base import (
    PickerContext,
    QPicker,
    QuestionUnit,
    StrictFormatDict,
    required_placeholders,
    seeded_rng,
    validate_placeholder_names,
)


class TemplateExpandError(ValueError):
    pass


def _render(fmt: str, values: dict[str, Any], *, template_id: str, field: str) -> str:
    try:
        needed = required_placeholders(fmt)
        validate_placeholder_names(needed)
        missing = [k for k in needed if k not in values]
        if missing:
            raise TemplateExpandError(f"Template '{template_id}' missing values for {field}: {missing}")
        return fmt.format_map(StrictFormatDict(values))
    except KeyError as e:
        raise TemplateExpandError(f"Template '{template_id}' missing value for {field}: {e}") from e
    except Exception as e:
        raise TemplateExpandError(f"Template '{template_id}' failed rendering {field}: {e}") from e


def _pick_value_for_var(
    *,
    seed: int,
    recipient: RecipientRow,
    question_index: int,
    template_id: str,
    var_name: str,
    rule: dict[str, Any],
    pools: dict[str, list[Any]],
) -> Any:
    method = (rule.get("method") or "cycle").strip()

    if "values" in rule:
        values = rule["values"]
        if not isinstance(values, list) or not values:
            raise TemplateExpandError(f"Template '{template_id}' var '{var_name}': values must be non-empty list")
    else:
        pool_name = rule.get("pool")
        if not isinstance(pool_name, str) or not pool_name.strip():
            raise TemplateExpandError(f"Template '{template_id}' var '{var_name}': must define pool or values")
        if pool_name not in pools:
            raise TemplateExpandError(f"Template '{template_id}' var '{var_name}': unknown pool '{pool_name}'")
        values = pools[pool_name]

    if method == "cycle":
        rng = seeded_rng("template_cycle", seed, template_id, var_name, recipient.email, question_index)
        idx = rng.randrange(0, 10**9) % len(values)
        return values[idx]
    if method == "random":
        rng = seeded_rng("template_random", seed, template_id, var_name, recipient.email, question_index)
        return rng.choice(values)

    raise TemplateExpandError(f"Template '{template_id}' var '{var_name}': unsupported method '{method}'")


@dataclass(frozen=True)
class TemplateExpandPicker(QPicker):
    templates: list[TemplateRow]
    param_vector: dict[str, Any]

    def generate_units(self, *, recipient: RecipientRow, context: PickerContext) -> list[QuestionUnit]:
        if context.k < 1:
            raise ValueError("k must be >= 1")
        if not self.templates:
            raise ValueError("templates must be non-empty")

        pv = parse_param_vector_json(self.param_vector)
        pools: dict[str, list[Any]] = pv["pools"]

        # Deterministic template ordering per campaign seed.
        rng = seeded_rng("template_expand", context.seed)
        templates = list(self.templates)
        rng.shuffle(templates)

        # Recipient-specific offset to spread templates.
        rrng = seeded_rng("template_expand", context.seed, recipient.email)
        start = rrng.randrange(0, len(templates))

        units: list[QuestionUnit] = []
        used: set[str] = set()
        idx = start
        while len(units) < context.k:
            t = templates[idx % len(templates)]
            idx += 1
            if len(self.templates) >= context.k and t.template_id in used:
                continue
            used.add(t.template_id)

            rules = t.rules or {}
            variables = rules.get("variables") or {}
            if not isinstance(variables, dict):
                raise TemplateExpandError(f"Template '{t.template_id}': rules.variables must be an object")

            values: dict[str, Any] = {}
            for var_name, rule in variables.items():
                if not isinstance(var_name, str) or not var_name.strip():
                    raise TemplateExpandError(f"Template '{t.template_id}': invalid variable name")
                if not isinstance(rule, dict):
                    raise TemplateExpandError(f"Template '{t.template_id}' var '{var_name}': rule must be an object")
                values[var_name] = _pick_value_for_var(
                    seed=context.seed,
                    recipient=recipient,
                    question_index=len(units) + 1,
                    template_id=t.template_id,
                    var_name=var_name,
                    rule=rule,
                    pools=pools,
                )

            vignette = _render(t.vignette_template, values, template_id=t.template_id, field="vignette_template")
            prompt = _render(t.prompt_template, values, template_id=t.template_id, field="prompt_template")

            rendered_choices: list[Choice] = []
            for c in t.choices:
                rendered_choices.append(
                    {"id": c["id"], "label": _render(c["label"], values, template_id=t.template_id, field="choice")}
                )

            units.append(
                QuestionUnit(
                    vignette_text=vignette,
                    prompt=prompt,
                    choices=rendered_choices,
                    tags=t.tags,
                    metadata={
                        "templateId": t.template_id,
                        "templateTags": t.tags,
                        "templateValues": values,
                    },
                )
            )

            if len(self.templates) < context.k and len(used) == len(self.templates):
                used.clear()

        return units


def picker_from_files(*, templates_csv_text: str, param_vector_obj: dict[str, Any]) -> TemplateExpandPicker:
    templates = parse_templates_csv(templates_csv_text)
    return TemplateExpandPicker(templates=templates, param_vector=param_vector_obj)


