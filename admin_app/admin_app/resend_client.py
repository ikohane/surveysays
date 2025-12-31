from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


class ResendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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
        raise ResendError(f"Resend API error {e.code} {e.reason}: {raw}", status_code=int(e.code), body=raw) from e
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


def publish_template(*, template_id: str) -> None:
    """
    Resend templates may be created in draft status; draft templates cannot be used to send.
    Best-effort publish with fallbacks because API shapes vary across accounts/SDK versions.
    """
    # Try the most obvious endpoint first.
    try:
        _request_json(method="POST", path=f"/templates/{template_id}/publish", body=None)
        return
    except ResendError:
        pass

    # Try PATCH with a status field (if supported).
    try:
        _request_json(method="PATCH", path=f"/templates/{template_id}", body={"status": "published"})
        return
    except ResendError:
        pass

    # Try POST publish without a trailing segment (some APIs use actions via query/field)
    try:
        _request_json(method="POST", path=f"/templates/{template_id}", body={"action": "publish"})
        return
    except ResendError:
        pass

    # If none worked, let the caller decide how to surface it.
    raise ResendError(
        "Unable to publish template automatically. Please publish the template in the Resend dashboard and retry.",
        status_code=None,
    )


def create_or_update_campaign_template(*, campaign_key: str, template_id: str | None, from_email: str, subject: str, html: str) -> str:
    variables = [
        {"key": "SURVEY_LINK", "type": "string", "fallbackValue": "http://127.0.0.1:5055/"},
        {"key": "CAMPAIGN_TITLE", "type": "string", "fallbackValue": campaign_key},
        {"key": "RECIPIENT_EMAIL", "type": "string", "fallbackValue": "recipient@example.com"},
        {"key": "FIRST_NAME", "type": "string", "fallbackValue": "First"},
        {"key": "LAST_NAME", "type": "string", "fallbackValue": "Last"},
    ]
    name = f"{campaign_key}"
    if template_id:
        try:
            update_template(template_id=template_id, name=name, from_email=from_email, subject=subject, html=html, variables=variables)
            publish_template(template_id=template_id)
            return template_id
        except ResendError:
            # If update fails (e.g. template missing), create a new one.
            pass
    new_id = create_template(name=name, from_email=from_email, subject=subject, html=html, variables=variables)
    publish_template(template_id=new_id)
    return new_id


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
    max_per_second: float = 0.8,
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
        first_name = str(inv.get("first_name") or "")
        last_name = str(inv.get("last_name") or "")
        survey_link = base_url.rstrip("/") + f"/s/{token}"

        now = time.monotonic()
        if now - last < min_interval:
            time.sleep(min_interval - (now - last))
        last = time.monotonic()

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = send_email_with_template(
                    to_email=FORCED_TEST_TO_EMAIL,
                    template_id=template_id,
                    variables={
                        "SURVEY_LINK": survey_link,
                        "CAMPAIGN_TITLE": campaign_title,
                        "RECIPIENT_EMAIL": intended_email,
                        "FIRST_NAME": first_name,
                        "LAST_NAME": last_name,
                    },
                )
                break
            except ResendError as e:
                # Retry on rate limits with exponential backoff.
                is_rate_limited = (e.status_code == 429) or ("429" in str(e)) or ("rate_limit" in str(e).lower())
                if is_rate_limited and attempt <= 6:
                    backoff = min(12.0, 0.5 * (2 ** (attempt - 1)))
                    time.sleep(backoff)
                    continue
                raise
        results.append({"intended_email": intended_email, "token": token, "resend_response": resp})
    return results


