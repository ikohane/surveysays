from __future__ import annotations

import csv
import io
from typing import Iterable

from .contracts import CaseRow, RecipientRow
from .csv_contracts import CsvContractError, parse_case_row, parse_recipient_row


def _read_csv_dicts(csv_text: str) -> Iterable[dict[str, str]]:
    """
    Read CSV text into dict rows using the first row as headers.
    All values are returned as strings (may be empty).
    """
    # Handle UTF-8 BOM if present
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text.lstrip("\ufeff")
    f = io.StringIO(csv_text, newline="")
    reader = csv.DictReader(f)
    if reader.fieldnames is None:
        raise CsvContractError("CSV missing header row")
    for row in reader:
        # DictReader can return None for missing keys; normalize to ""
        yield {k: (v if v is not None else "") for k, v in row.items()}


def parse_cases_csv(csv_text: str) -> list[CaseRow]:
    cases: list[CaseRow] = []
    for idx, row in enumerate(_read_csv_dicts(csv_text), start=2):  # 1-based header; data starts at line 2
        row_name = f"cases.csv line {idx}"
        cases.append(parse_case_row(row, row_name=row_name))
    if not cases:
        raise CsvContractError("cases.csv contains no rows")
    return cases


def parse_recipients_csv(csv_text: str) -> list[RecipientRow]:
    recs: list[RecipientRow] = []
    for idx, row in enumerate(_read_csv_dicts(csv_text), start=2):
        row_name = f"recipients.csv line {idx}"
        recs.append(parse_recipient_row(row, row_name=row_name))
    if not recs:
        raise CsvContractError("recipients.csv contains no rows")
    # Ensure unique emails
    emails = [r.email for r in recs]
    if len(set(emails)) != len(emails):
        raise CsvContractError("recipients.csv contains duplicate emails")
    return recs


