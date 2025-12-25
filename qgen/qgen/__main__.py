from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generator import generate_bulk_payload
from .io_csv import parse_cases_csv, parse_recipients_csv


def main() -> None:
    p = argparse.ArgumentParser(prog="qgen", description="Generate per-recipient questionnaire variants.")
    p.add_argument("--campaign-key", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--version", type=int, default=1)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--cases-csv", required=True)
    p.add_argument("--recipients-csv", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    cases = parse_cases_csv(Path(args.cases_csv).read_text(encoding="utf-8"))
    recipients = parse_recipients_csv(Path(args.recipients_csv).read_text(encoding="utf-8"))
    payload = generate_bulk_payload(
        campaign_key=args.campaign_key,
        title=args.title,
        questionnaire_version=args.version,
        cases=cases,
        recipients=recipients,
        seed=args.seed,
    )
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()


