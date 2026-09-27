"""Google API errors without secrets: httpx's own messages include the request URL,
which for key-in-query APIs contains the API key."""

from __future__ import annotations

import httpx


class GoogleAPIError(RuntimeError):
    pass


def raise_for_google(response: httpx.Response, api: str) -> None:
    if response.is_success:
        return
    try:
        err = response.json().get("error", {})
        detail = f"{err.get('status', '')}: {err.get('message', '')}".strip(": ")
    except ValueError:
        detail = response.reason_phrase
    raise GoogleAPIError(f"{api} returned HTTP {response.status_code} ({detail[:300]})")


def redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text
