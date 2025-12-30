import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import time

import pandas as pd
import resend
from dotenv import load_dotenv


EXCEL_PATH = "datasheets/HVPInvite1.xlsx"
FROM_EMAIL = "Zak Kohane<zak@study.hvp.global>"
BCC_EMAILS = ["payal@mit.edu", "kohane@gmail.com"]
TEMPLATE_VARIABLE_MAX_CHARS = 2000
SEND_LOG_PATH = os.getenv("SEND_LOG_PATH", "out/send_log.jsonl")


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "If it's stored in a .env file, install python-dotenv and ensure send.py is run from the directory that contains .env."
        )
    return val


def _append_send_log(
    *,
    from_email: str,
    to_emails: list[str],
    cc_emails: list[str],
    bcc_emails: list[str],
    template_id_or_alias: str,
    resolved_template_id: str,
    spreadsheet_row: int | None,
    resend_response: object,
) -> None:
    """
    Append a single JSON line for each successfully sent email.
    We use JSONL (one JSON object per line) so it stays easy to append and parse.
    """
    import json

    ts = datetime.now(timezone.utc).isoformat()
    payload = {
        "timestamp": ts,
        "from": from_email,
        "to": to_emails,
        "cc": cc_emails,
        "bcc": bcc_emails,
        "template_id_or_alias": template_id_or_alias,
        "resolved_template_id": resolved_template_id,
        "spreadsheet_row": spreadsheet_row,
        "resend_response": resend_response,
    }

    p = Path(SEND_LOG_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _coerce_str(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v)


def _truncate_for_template(value: str, *, key: str, max_chars: int, strict: bool) -> str:
    if len(value) <= max_chars:
        return value
    msg = f"Template variable '{key}' exceeds Resend's {max_chars} character limit (got {len(value)})."
    if strict:
        raise ValueError(msg + " Refusing to send. Shorten the value or redesign the template variables.")
    truncated = value[: max_chars - 1] + "…"
    print(f"WARNING: {msg} Truncating for send.py test run.")
    return truncated


def _split_three_questions(raw: str) -> tuple[str, str, str]:
    """
    The Excel `questions` cell contains 3 questions, typically like:

      Question 1. ...
      ...
      Question 2. ...
      ...
      Question 3. ...

    Return (q1, q2, q3) with the "Question N." header removed.
    If parsing fails, returns (raw, "", "").
    """
    text = (raw or "").strip()
    if not text:
        return "", "", ""

    # Match "Question 1." / "Question 1:" with flexible whitespace and line starts.
    pattern = re.compile(r"(^|\n)\s*Question\s*([1-3])\s*[\.:]\s*", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return text, "", ""

    # Build slices per question number.
    parts: dict[str, str] = {}
    for i, m in enumerate(matches):
        qnum = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts[qnum] = text[start:end].strip()

    def _strip_leading_question_label(s: str) -> str:
        # Extra safety: remove any repeated "Question N" label at the start of the chunk.
        return re.sub(r"^\s*Question\s*[1-3]\s*[\.:]\s*", "", (s or "").strip(), flags=re.IGNORECASE).strip()

    return (
        _strip_leading_question_label(parts.get("1", "")),
        _strip_leading_question_label(parts.get("2", "")),
        _strip_leading_question_label(parts.get("3", "")),
    )


def _split_stem_and_choices(question_text: str) -> tuple[str, dict[str, str]]:
    """
    Split a question chunk into a stem (vignette) and choices keyed by letter.

    Example expected inside each chunk:
      ... vignette ...
      Choice A. ...
      Choice B. ...
    """
    s = (question_text or "").strip()
    if not s:
        return "", {}

    choice_re = re.compile(r"\bChoice\s+([A-Z])\.\s*", re.IGNORECASE)
    matches = list(choice_re.finditer(s))
    if not matches:
        return s, {}

    stem = s[: matches[0].start()].strip()
    choices: dict[str, str] = {}
    for i, m in enumerate(matches):
        letter = (m.group(1) or "").upper()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s)
        body = s[start:end].strip()
        if letter:
            # Keep full label for easy templating: "Choice A. ..."
            choices[letter] = f"Choice {letter}. {body}".strip()

    return stem, choices


def _format_question_html(stem: str, choices: dict[str, str]) -> str:
    """
    Resend templates are HTML; newline characters in variables are usually collapsed by HTML rendering.
    To guarantee visible line breaks, we inject <br/> tags.

    Output format:
      <stem>
      <blank line>
      Choice A. ...
      <blank line>
      Choice B. ...
    """
    stem = (stem or "").strip()
    if not choices:
        return stem

    out = stem
    # Blank line between stem and first choice
    out += "<br/><br/>"

    # Prefer A then B, then any remaining letters
    order = ["A", "B"] + sorted([k for k in choices.keys() if k not in {"A", "B"}])
    first = True
    for letter in order:
        val = (choices.get(letter) or "").strip()
        if not val:
            continue
        if not first:
            out += "<br/><br/>"
        out += val
        first = False
    return out.strip()


def _resolve_template_id(template_id_or_alias: str) -> str:
    """
    Resend Emails API requires a template *id*.
    The dashboard also exposes a template *alias*; if the caller provides an alias, resolve it to the id.
    """
    template_id_or_alias = (template_id_or_alias or "").strip()
    if not template_id_or_alias:
        raise ValueError("Missing template id/alias.")

    # If it's already a UUID, assume it's the id.
    if len(template_id_or_alias) == 36 and template_id_or_alias.count("-") == 4:
        return template_id_or_alias

    # Otherwise treat as alias and resolve via Templates.list()
    wanted = template_id_or_alias.lower()
    resp = resend.Templates.list()
    items = resp.get("data", []) if isinstance(resp, dict) else resp
    for t in items:
        if (t.get("alias") or "").strip().lower() == wanted:
            return t["id"]
    raise RuntimeError(
        f"Template alias '{template_id_or_alias}' not found in this Resend account. "
        "Double-check the alias and that RESEND_API_KEY points at the correct Resend workspace."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one test email via Resend using the first row of an Excel sheet.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be sent using row 2 (df.iloc[0]) without sending an email.",
    )
    parser.add_argument(
        "--strict-template-vars",
        action="store_true",
        help="Fail (instead of truncating) if any template variable exceeds Resend's per-value length limit.",
    )
    parser.add_argument(
        "--template-id",
        default=os.getenv("RESEND_TEMPLATE_ID", "hvpinvite1"),
        help=(
            "Resend template ID to use (NOT the template name). "
            "You can also set RESEND_TEMPLATE_ID in your environment/.env."
        ),
    )
    parser.add_argument(
        "--from-email",
        default=os.getenv("RESEND_FROM_EMAIL", FROM_EMAIL),
        help="From email in the format 'Name <email@domain>' (must be a verified sender in Resend).",
    )
    parser.add_argument(
        "--max-requests-per-second",
        type=float,
        default=1.8,
        help="Throttle sending to avoid Resend rate limits (default 1.8 req/sec).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries per row when rate-limited (default 5).",
    )
    args = parser.parse_args()

    # Load RESEND_API_KEY from .env if present (otherwise falls back to existing environment variables)
    load_dotenv()
    resend.api_key = _require_env("RESEND_API_KEY")

    # Read spreadsheet (xlsx requires openpyxl)
    df = pd.read_excel(EXCEL_PATH, engine="openpyxl")
    df.columns = [str(c).strip().lower() for c in df.columns]

    required_columns = {"email", "firstname", "questions"}
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"Excel sheet is missing required columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("Excel sheet has no rows.")

    # Resolve template once (accept alias like hvpinvite1).
    resolved_template_id = _resolve_template_id(args.template_id)

    # Build list of rows to send: any row with a non-empty email.
    rows_to_send: list[tuple[int, pd.Series, str]] = []
    for df_index, row in df.iterrows():
        raw_email = _coerce_str(row.get("email", "")).strip()
        if not raw_email or raw_email.lower() == "nan":
            continue
        rows_to_send.append((df_index, row, raw_email))

    if not rows_to_send:
        raise ValueError("No non-empty email rows found in the spreadsheet.")

    if args.dry_run:
        print("DRY RUN (no email sent)")
        print(f"template.id={args.template_id} (resolved={resolved_template_id})")
        print(f"rows_to_send={len(rows_to_send)}")
        print("example_to=" + ", ".join([e for _, _, e in rows_to_send[:3]]))
        return 0

    sent = 0
    failed = 0
    failed_sends: list[tuple[int, str, str]] = []
    min_interval = 1.0 / max(args.max_requests_per_second, 0.1)
    last_send_started_at = 0.0
    for df_index, row, email_to in rows_to_send:
        firstname = _coerce_str(row.get("firstname", "")).strip()
        questions = _coerce_str(row.get("questions", "")).strip()

        firstname = _truncate_for_template(
            firstname, key="firstname", max_chars=TEMPLATE_VARIABLE_MAX_CHARS, strict=args.strict_template_vars
        )

        q1, q2, q3 = _split_three_questions(questions)
        q1_stem, q1_choices = _split_stem_and_choices(q1)
        q2_stem, q2_choices = _split_stem_and_choices(q2)
        q3_stem, q3_choices = _split_stem_and_choices(q3)

        q1_formatted = _truncate_for_template(
            _format_question_html(q1_stem, q1_choices),
            key="question1",
            max_chars=TEMPLATE_VARIABLE_MAX_CHARS,
            strict=args.strict_template_vars,
        )
        q2_formatted = _truncate_for_template(
            _format_question_html(q2_stem, q2_choices),
            key="question2",
            max_chars=TEMPLATE_VARIABLE_MAX_CHARS,
            strict=args.strict_template_vars,
        )
        q3_formatted = _truncate_for_template(
            _format_question_html(q3_stem, q3_choices),
            key="question3",
            max_chars=TEMPLATE_VARIABLE_MAX_CHARS,
            strict=args.strict_template_vars,
        )

        params: resend.Emails.SendParams = {
            "from": args.from_email,
            "to": [email_to],
            "bcc": BCC_EMAILS,
            "template": {
                "id": resolved_template_id,
                "variables": {
                    "firstname": firstname,
                    "question1": q1_formatted,
                    "question2": q2_formatted,
                    "question3": q3_formatted,
                },
            },
        }

        spreadsheet_row = int(df_index) + 2  # +1 for header, +1 for 1-based Excel rows

        # Throttle to stay under rate limit
        now = time.monotonic()
        elapsed = now - last_send_started_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        # Retry on rate limit errors
        attempt = 0
        while True:
            attempt += 1
            last_send_started_at = time.monotonic()
            try:
                result = resend.Emails.send(params)
                break
            except Exception as e:
                msg = str(e)
                is_rate_limited = ("Too many requests" in msg) or ("rate limit" in msg.lower()) or ("429" in msg)
                if is_rate_limited and attempt <= args.max_retries:
                    backoff = min(8.0, 0.5 * (2 ** (attempt - 1)))
                    print(
                        f"RATE_LIMIT row={spreadsheet_row} to={email_to} attempt={attempt}/{args.max_retries} "
                        f"sleeping={backoff:.1f}s"
                    )
                    time.sleep(backoff)
                    continue
                failed += 1
                failed_sends.append((spreadsheet_row, email_to, msg))
                print(f"FAILED row={spreadsheet_row} to={email_to}: {e}")
                result = None
                break

        if result is None:
            continue

        _append_send_log(
            from_email=params["from"],
            to_emails=params["to"] if isinstance(params["to"], list) else [params["to"]],
            cc_emails=[],
            bcc_emails=BCC_EMAILS,
            template_id_or_alias=args.template_id,
            resolved_template_id=resolved_template_id,
            spreadsheet_row=spreadsheet_row,
            resend_response=result,
        )
        sent += 1
        print(f"Sent row={spreadsheet_row} to={email_to}: {result}")

    print(f"Done. sent={sent} failed={failed} total={len(rows_to_send)}")
    if failed_sends:
        print("Failed recipients:")
        for row_num, email, err in failed_sends:
            print(f"- row={row_num} email={email} error={err}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
