from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """
    Deterministic JSON encoding suitable for hashing.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def questionnaire_hash(questionnaire_json: Any) -> str:
    return sha256_hex(canonical_json(questionnaire_json))




