from __future__ import annotations

import json
from pathlib import Path

from qgen.io_csv import parse_cases_csv, parse_recipients_csv
from qgen.generator import generate_bulk_payload


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    cases_path = repo_root / "sample_data" / "cases.csv"
    recs_path = repo_root / "sample_data" / "recipients.csv"

    cases = parse_cases_csv(cases_path.read_text(encoding="utf-8"))
    recipients = parse_recipients_csv(recs_path.read_text(encoding="utf-8"))

    payload = generate_bulk_payload(
        campaign_key="demo_campaign",
        title="Clinical Case Decision Survey",
        questionnaire_version=1,
        cases=cases,
        recipients=recipients,
        seed=12345,
    )

    out_path = repo_root / "out" / "bulk_invitations.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(payload['invitations'])} invitations to {out_path}")


if __name__ == "__main__":
    main()




