from __future__ import annotations

import csv
import io
import re
from pathlib import Path


class CaseGenError(ValueError):
    pass


def _nonempty_rows(rows: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for r in rows:
        if any((c or "").strip() for c in r):
            out.append([str(c or "") for c in r])
    return out


def _meta_value(row: list[str], *, row_name: str) -> str:
    """
    For metadata rows (title/prompt/choice labels), accept either:
    - a single-cell row: ["My Title"]
    - a 2+ cell row: ["Title", "My Title"] (we take the last non-empty cell)
    """
    for c in reversed(row):
        v = (c or "").strip()
        if v:
            return v
    raise CaseGenError(f"{row_name}: missing value")


def _slugify_filename(study_title: str) -> str:
    """
    Convert a study title into a safe-ish filename stem.
    """
    s = study_title.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        raise CaseGenError("Row 1 (study title) produces an empty filename after sanitization")
    return s


def _parse_study_spec_rows(rows: list[list[str]]) -> tuple[str, str, str, str, list[tuple[str, str]]]:
    """
    Input format (no header):
      - Row 1: study title (used for output filename)
      - Row 2: prompt (constant)
      - Row 3: choice A label (constant)
      - Row 4: choice B label (constant)
      - Row 5+: pairs of case texts: (col1, col2)
    """
    rows = _nonempty_rows(rows)
    if len(rows) < 5:
        raise CaseGenError(
            "Study spec CSV must have at least 5 non-empty rows (title, prompt, choice A, choice B, and 1+ case row)"
        )

    title = _meta_value(rows[0], row_name="Row 1 (study title)")
    prompt = _meta_value(rows[1], row_name="Row 2 (prompt)")
    choice_a = _meta_value(rows[2], row_name="Row 3 (choice A label)")
    choice_b = _meta_value(rows[3], row_name="Row 4 (choice B label)")

    pairs: list[tuple[str, str]] = []
    for i, r in enumerate(rows[4:], start=5):
        cells = [(c or "").strip() for c in r]
        nonempty = [c for c in cells if c]
        if len(nonempty) < 2:
            raise CaseGenError(f"Row {i}: expected at least 2 columns (choice A text, choice B text)")
        pairs.append((nonempty[0], nonempty[1]))

    if not pairs:
        raise CaseGenError("No case pairs found (rows 5+)")

    return title, prompt, choice_a, choice_b, pairs


def caseGen_text(
    study_spec_csv_text: str,
    *,
    output_dir: str | Path | None = None,
) -> Path:
    """
    Create a standard `cases.csv` file (the format accepted by the Admin app) from a study-spec CSV text.

    Output:
      - Written to `sample_data/` by default
      - Filename derived from Row 1 (study title): `<slug>.cases.csv`

    Cases format (matches existing sample header):
      case_id,vignette,prompt,choice_A,choice_B,choice_C,choice_D,tags

    Vignette formatting:
      vignette = "<col1>\\n\\n<col2>"
    """
    # Handle UTF-8 BOM if present
    if study_spec_csv_text.startswith("\ufeff"):
        study_spec_csv_text = study_spec_csv_text.lstrip("\ufeff")

    reader = csv.reader(io.StringIO(study_spec_csv_text, newline=""))
    rows = [[c for c in r] for r in reader]
    title, prompt, choice_a, choice_b, pairs = _parse_study_spec_rows(rows)

    repo_root = Path(__file__).resolve().parents[2]
    out_dir = Path(output_dir) if output_dir is not None else (repo_root / "sample_data")
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = _slugify_filename(title)
    out_path = out_dir / f"{stem}.cases.csv"

    width = max(3, len(str(len(pairs))))
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["case_id", "vignette", "prompt", "choice_A", "choice_B", "choice_C", "choice_D", "tags"],
            lineterminator="\n",
        )
        writer.writeheader()
        for idx, (a_text, b_text) in enumerate(pairs, start=1):
            vignette = f"{a_text}\n\n{b_text}"
            writer.writerow(
                {
                    "case_id": f"case_{idx:0{width}d}",
                    "vignette": vignette,
                    "prompt": prompt,
                    "choice_A": choice_a,
                    "choice_B": choice_b,
                    "choice_C": "",
                    "choice_D": "",
                    "tags": "",
                }
            )

    return out_path


def caseGen(
    study_spec_csv_path: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> Path:
    """
    File-path wrapper for `caseGen_text`.
    """
    p = Path(study_spec_csv_path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except Exception as e:
        raise CaseGenError(f"Failed to read study spec CSV at {p}: {e}") from e
    return caseGen_text(text, output_dir=output_dir)




