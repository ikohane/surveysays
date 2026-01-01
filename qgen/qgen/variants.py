from __future__ import annotations

import random
from dataclasses import dataclass

from .contracts import CaseRow, RecipientRow


@dataclass(frozen=True)
class VariantAssignment:
    recipient: RecipientRow
    case: CaseRow


def assign_cases_round_robin(
    *,
    cases: list[CaseRow],
    recipients: list[RecipientRow],
    seed: int,
) -> list[VariantAssignment]:
    """
    Deterministic, balanced assignment:
      - recipients are sorted by email (stable)
      - cases are shuffled by seed (stable)
      - assign cases in round-robin order

    This yields reproducible variants and tends to distribute cases evenly.
    """
    if not cases:
        raise ValueError("cases must be non-empty")
    if not recipients:
        raise ValueError("recipients must be non-empty")

    recs = sorted(recipients, key=lambda r: r.email)
    cases_shuffled = list(cases)
    rng = random.Random(seed)
    rng.shuffle(cases_shuffled)

    out: list[VariantAssignment] = []
    for i, r in enumerate(recs):
        out.append(VariantAssignment(recipient=r, case=cases_shuffled[i % len(cases_shuffled)]))
    return out




