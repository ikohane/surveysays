"""
Utility functions extracted from app.py for maintainability.

These are pure functions with minimal dependencies, suitable for reuse.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any


def get_cloud_config() -> tuple[str, str]:
    """
    Returns (cloud_base_url, cloud_admin_token) from environment variables.
    """
    base = (os.environ.get("CLOUDFLARE_STUDY_BASE_URL") or "").strip().rstrip("/")
    token = (os.environ.get("CLOUDFLARE_ADMIN_TOKEN") or "").strip()
    return base, token


def get_railway_config() -> tuple[str, str]:
    """
    Returns (railway_base_url, railway_admin_token) from environment variables.
    For Railway deployments of online_assign campaigns.
    """
    base = (os.environ.get("RAILWAY_APP_URL") or "").strip().rstrip("/")
    token = (os.environ.get("RAILWAY_ADMIN_TOKEN") or "").strip()
    return base, token


def parse_simple_yaml_to_obj(text: str) -> dict[str, Any]:
    """
    Minimal YAML mapping parser for our small config shape.
    - Supports nested maps via indentation (2+ spaces).
    - Supports scalars: bool, int, float, string.
    - Ignores blank lines and comments.
    If PyYAML is installed, we prefer it.
    """
    s = (text or "").strip()
    if not s:
        return {}
    try:
        import yaml  # type: ignore

        obj = yaml.safe_load(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    def parse_scalar(v: str) -> Any:
        v = v.strip()
        if not v:
            return ""
        low = v.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        try:
            if "." in v:
                return float(v)
            return int(v)
        except Exception:
            return v.strip('"').strip("'")

    lines = []
    for raw in s.splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        # strip inline comments (naive): only if preceded by space
        if " #" in raw:
            raw = raw.split(" #", 1)[0]
        lines.append(raw.rstrip("\n"))

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, root)]

    for raw in lines:
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        val = rest.strip()

        # pop to correct indent level
        while stack and indent < stack[-1][0]:
            stack.pop()
        if not stack:
            stack = [(0, root)]
        cur = stack[-1][1]

        if val == "":
            child: dict[str, Any] = {}
            cur[key] = child
            stack.append((indent + 2, child))
        else:
            cur[key] = parse_scalar(val)

    return root


def canonical_json_bytes(obj: Any) -> bytes:
    """Deterministic JSON encoding for hashing requests."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def cloud_post_json(*, url: str, bearer_token: str, payload_obj: Any, timeout_sec: int = 30) -> dict[str, Any]:
    """POST JSON to a cloud endpoint with bearer auth."""
    data = canonical_json_bytes(payload_obj)
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        # macOS/Homebrew Python often lacks a system CA bundle; prefer certifi if available.
        cafile: str | None = None
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
        except Exception:
            cafile = None

        ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception as e:
                raise RuntimeError(f"Cloud response is not JSON (status {resp.status}): {body[:300]}") from e
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise RuntimeError(f"Cloud HTTP {e.code}: {body[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cloud connection error: {e}") from e


def cloud_get_json(*, url: str, bearer_token: str, timeout_sec: int = 30) -> dict[str, Any]:
    """GET JSON from a cloud endpoint with bearer auth."""
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        },
    )
    try:
        # macOS/Homebrew Python often lacks a system CA bundle; prefer certifi if available.
        cafile: str | None = None
        try:
            import certifi  # type: ignore

            cafile = certifi.where()
        except Exception:
            cafile = None

        ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

        with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception as e:
                raise RuntimeError(f"Cloud response is not JSON (status {resp.status}): {body[:300]}") from e
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        raise RuntimeError(f"Cloud HTTP {e.code}: {body[:600]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cloud connection error: {e}") from e


def render_email_preview(*, html: str, variables: dict[str, str]) -> str:
    """
    Very small, safe placeholder replacement for triple-brace variables.
    We do not execute any HTML/JS; the template itself is HTML, we just substitute strings.
    """
    out = html
    for k, v in variables.items():
        out = out.replace(f"{{{{{{{k}}}}}}}", v)
    return out


def email_config_to_yaml(*, from_email: str, subject: str, base_url: str, html: str) -> str:
    """Serialize email config back to YAML format."""
    lines = []
    lines.append(f'from: "{from_email}"')
    lines.append(f'subject: "{subject}"')
    lines.append(f'base_url: "{base_url}"')
    lines.append("html: |")
    for line in html.split("\n"):
        lines.append(f"  {line}")
    return "\n".join(lines)


def parse_json_obj(text: str | None) -> dict[str, Any]:
    """
    Parse a JSON string into a dict; returns {} on empty/invalid/non-object.
    Useful for resilient rendering of user-supplied / DB-stored JSON blobs.
    """
    s = (text or "").strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}

