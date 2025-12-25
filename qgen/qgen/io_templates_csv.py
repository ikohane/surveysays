from __future__ import annotations

import csv
import io
from typing import Iterable

from .templates_contracts import TemplateRow, TemplatesContractError, parse_template_row


def _read_csv_dicts(csv_text: str) -> Iterable[dict[str, str]]:
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text.lstrip("\ufeff")
    f = io.StringIO(csv_text, newline="")
    reader = csv.DictReader(f)
    if reader.fieldnames is None:
        raise TemplatesContractError("templates.csv missing header row")
    for row in reader:
        yield {k: (v if v is not None else "") for k, v in row.items()}


def parse_templates_csv(csv_text: str) -> list[TemplateRow]:
    templates: list[TemplateRow] = []
    for idx, row in enumerate(_read_csv_dicts(csv_text), start=2):
        row_name = f"templates.csv line {idx}"
        templates.append(parse_template_row(row, row_name=row_name))
    if not templates:
        raise TemplatesContractError("templates.csv contains no rows")
    ids = [t.template_id for t in templates]
    if len(set(ids)) != len(ids):
        raise TemplatesContractError("templates.csv contains duplicate template_id values")
    return templates


