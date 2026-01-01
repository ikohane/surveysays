from __future__ import annotations

from dataclasses import dataclass

from ..contracts import CaseRow, RecipientRow
from .base import PickerContext, QPicker, QuestionUnit, seeded_rng


@dataclass(frozen=True)
class PickKCasesPicker(QPicker):
    cases: list[CaseRow]

    def generate_units(self, *, recipient: RecipientRow, context: PickerContext) -> list[QuestionUnit]:
        if context.k < 1:
            raise ValueError("k must be >= 1")
        if not self.cases:
            raise ValueError("cases must be non-empty")

        # Deterministic case ordering per campaign seed.
        rng = seeded_rng("pick_k_cases", context.seed)
        cases = list(self.cases)
        rng.shuffle(cases)

        # Deterministic offset per recipient to spread load.
        rrng = seeded_rng("pick_k_cases", context.seed, recipient.email)
        start = rrng.randrange(0, len(cases))

        units: list[QuestionUnit] = []
        used: set[str] = set()
        idx = start
        while len(units) < context.k:
            case = cases[idx % len(cases)]
            idx += 1
            if len(self.cases) >= context.k and case.case_id in used:
                continue
            used.add(case.case_id)
            units.append(
                QuestionUnit(
                    vignette_text=case.vignette,
                    prompt=case.prompt,
                    choices=case.choices,
                    tags=case.tags,
                    metadata={"caseId": case.case_id, "caseTags": case.tags},
                )
            )
            if len(self.cases) < context.k and len(used) == len(self.cases):
                # All unique cases exhausted; further units will repeat (expected).
                used.clear()

        return units




