from __future__ import annotations

import argparse
from pathlib import Path

from qgen.casegen import caseGen


def _main() -> None:
    ap = argparse.ArgumentParser(description="Convert a study-spec CSV into a standard cases.csv file.")
    ap.add_argument(
        "study_spec_csv",
        help="Path to the study-spec CSV (Row1 title, Row2 prompt, Row3 choiceA label, Row4 choiceB label, Row5+ case pairs).",
    )
    ap.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (defaults to repo sample_data/).",
    )
    args = ap.parse_args()

    out = caseGen(args.study_spec_csv, output_dir=(Path(args.out_dir) if args.out_dir else None))
    print(str(out))


if __name__ == "__main__":
    _main()




