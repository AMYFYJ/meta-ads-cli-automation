"""Lookup helpers for detailed-targeting IDs (interests, behaviors, demographics).

The workbook's `interests`/`behaviors` columns need numeric Meta targeting IDs
(written as `<id>:<Name>`); this module backs the `targeting-search` CLI command
that finds them without leaving the terminal.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from .adapters import redact_secrets, with_retry

# Graph search classes for the non-interest kinds. Interests use the dedicated
# type=adinterest search, which supports server-side q= matching.
_CATEGORY_CLASSES = {
    "behavior": "behaviors",
    "demographic": "demographics",
    "life_event": "life_events",
    "industry": "industries",
    "income": "income",
    "family_status": "family_statuses",
}

SEARCH_KINDS = ("interest", *_CATEGORY_CLASSES)


def search_targeting(query: str, kind: str = "interest", limit: int = 25) -> list[dict[str, Any]]:
    """Return [{id, name, audience_size, path}] for a search term.

    Requires ACCESS_TOKEN in the environment (same token the live pipeline uses).
    """
    token = os.environ.get("ACCESS_TOKEN")
    if not token:
        raise RuntimeError("ACCESS_TOKEN is required for targeting search (load .env first).")
    if kind not in SEARCH_KINDS:
        raise ValueError(f"Unknown kind '{kind}'. Use one of: {', '.join(SEARCH_KINDS)}")
    version = os.environ.get("META_API_VERSION", "v21.0")
    params: dict[str, str] = {"access_token": token, "limit": str(max(limit, 25))}
    if kind == "interest":
        params.update({"type": "adinterest", "q": query})
    else:
        # adTargetingCategory has no q= filter; fetch the class and match locally.
        params.update({"type": "adTargetingCategory", "class": _CATEGORY_CLASSES[kind], "limit": "500"})
    url = f"https://graph.facebook.com/{version}/search?{urllib.parse.urlencode(params)}"
    try:
        ok, message, body = with_retry(lambda: _get(url))
    except Exception as exc:  # noqa: BLE001 - surface Meta errors with the token redacted.
        raise RuntimeError(f"Targeting search failed: {redact_secrets(str(exc))}") from exc
    if not ok:
        raise RuntimeError(f"Targeting search failed: {redact_secrets(message)}")
    rows = json.loads(body).get("data", [])
    needle = query.strip().lower()
    results = []
    for row in rows:
        name = str(row.get("name", ""))
        if kind != "interest" and needle and needle not in name.lower():
            continue
        results.append(
            {
                "id": str(row.get("id", "")),
                "name": name,
                "audience_size": row.get("audience_size_upper_bound") or row.get("audience_size") or "",
                "path": " > ".join(row.get("path", [])) if isinstance(row.get("path"), list) else str(row.get("path") or ""),
            }
        )
        if len(results) >= limit:
            break
    return results


def workbook_cell(results: list[dict[str, Any]]) -> str:
    """The comma-separated `<id>:<Name>` string to paste into an interests/behaviors cell."""
    return ", ".join(f"{row['id']}:{row['name']}" for row in results if row["id"])


def _get(url: str) -> tuple[bool, str, str]:
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read().decode("utf-8")
    return True, body, body
