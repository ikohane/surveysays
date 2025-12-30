from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


class ResendError(RuntimeError):
    pass


RESEND_API_BASE = "https://api.resend.com"
FORCED_TEST_TO_EMAIL = "kohane@gmail.com"


def _require_api_key() -> str:
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key:
        raise ResendError("RESEND_API_KEY is not set")
    return key


def _request_json(*, method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    api_key = _require_api_key()
    url = RESEND_API_BASE + path
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if hasattr(e, "read") else ""
        raise ResendError(f"Resend API error {e.code} {e.reason}: {raw}") from e
    except urllib.error.URLError as e:
        raise ResendError(f"Resend API connection error: {e}") from e


def create_template(*, name: str, from_email: str, subject: str, html: str, variables: list[dict[str, Any]]) -> str:
    """
    Returns created template id.
    """
    payload = {
        "name": name,
        "from": from_email,
        "subject": subject,
        "html": html,
        "variables": variables,
    }
    resp = _request_json(method="POST", path="/templates", body=payload)
    template_id = (resp.get("id") or resp.get("data", {}).get("id") or "").strip() if isinstance(resp, dict) else ""
    if not template_id:
        raise ResendError(f"Unexpected template create response: {resp}")
    return template_id


def update_template(*, template_id: str, name: str, from_email: str, subject: str, html: str, variables: list[dict[str, Any]]) -> None:
    """
    Best-effort update. Resend supports updating templates; endpoint shape may vary by account version.
    """
    payload = {
        "name": name,
        "from": from_email,
        "subject": subject,
        "html": html,
        "variables": variables,
    }
    # Try PATCH then PUT as fallback.
    try:
        _request_json(method="PATCH", path=f"/templates/{template_id}", body=payload)
        return
    except ResendError:
        _request_json(method="PUT", path=f"/templates/{template_id}", body=payload)


def create_or_update_campaign_template(*, campaign_key: str, template_id: str | None, from_email: str, subject: str, html: str) -> str:
    variables = [
        {"key": "SURVEY_LINK", "type": "string", "fallbackValue": "http://127.0.0.1:5055/"},
        {"key": "CAMPAIGN_TITLE", "type": "string", "fallbackValue": campaign_key},
        {"key": "RECIPIENT_EMAIL", "type": "string", "fallbackValue": "recipient@example.com"},
    ]
    name = f"{campaign_key}"
    if template_id:
        try:
            update_template(template_id=template_id, name=name, from_email=from_email, subject=subject, html=html, variables=variables)
            return template_id
        except ResendError:
            # If update fails (e.g. template missing), create a new one.
            pass
    return create_template(name=name, from_email=from_email, subject=subject, html=html, variables=variables)


def send_email_with_template(*, to_email: str, template_id: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = {"to": [to_email], "template_id": template_id, "variables": variables}
    # Some Resend accounts accept template as {id, variables}; others accept template_id + variables.
    # Try the more modern form first, then fallback.
    try:
        return _request_json(method="POST", path="/emails", body=payload)
    except ResendError:
        payload2 = {"to": [to_email], "template": {"id": template_id, "variables": variables}}
        return _request_json(method="POST", path="/emails", body=payload2)


def send_invites_for_campaign(
    *,
    template_id: str,
    campaign_title: str,
    base_url: str,
    invitations: list[dict[str, Any]],
    max_per_second: float = 1.5,
) -> list[dict[str, Any]]:
    """
    Safety gate: all messages are forced to FORCED_TEST_TO_EMAIL.
    Intended recipient email is passed via RECIPIENT_EMAIL variable.
    """
    results: list[dict[str, Any]] = []
    min_interval = 1.0 / max(max_per_second, 0.1)
    last = 0.0
    for inv in invitations:
        intended_email = str(inv.get("email") or "")
        token = str(inv.get("token") or "")
        survey_link = base_url.rstrip("/") + f"/s/{token}"

        now = time.monotonic()
        if now - last < min_interval:
            time.sleep(min_interval - (now - last))
        last = time.monotonic()

        resp = send_email_with_template(
            to_email=FORCED_TEST_TO_EMAIL,
            template_id=template_id,
            variables={
                "SURVEY_LINK": survey_link,
                "CAMPAIGN_TITLE": campaign_title,
                "RECIPIENT_EMAIL": intended_email,
            },
        )
        results.append({"intended_email": intended_email, "token": token, "resend_response": resp})
    return results


