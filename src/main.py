"""Apify lifecycle and persistent snapshot processing."""

from datetime import datetime, timezone

import httpx
from apify import Actor

from .monitor import (
    USER_AGENT,
    compare,
    fetch_text,
    snapshot_key,
    text_hash,
    validate_input,
)

STORE_NAME = "changewatch-snapshots-v1"


async def process_competitor(client_id: str, competitor: dict, store, client, push_data) -> bool:
    """Emit before advancing baseline; storage/output failures fail the run."""
    url = competitor["url"]
    key = snapshot_key(client_id, url)
    previous = await store.get_value(key)
    if previous is not None and (
        not isinstance(previous, dict)
        or previous.get("schema_version") != 1
        or previous.get("client_id") != client_id
        or previous.get("url") != url
        or not isinstance(previous.get("text"), str)
        or previous.get("hash") != text_hash(previous["text"])
    ):
        raise ValueError(f"Invalid snapshot record {key}; inspect it before resetting")
    result = {
        "client_id": client_id,
        "competitor": competitor["name"],
        "url": url,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_key": key,
    }
    try:
        current = await fetch_text(client, url)
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        # Avoid including full request URLs (which may carry query secrets) in errors.
        message = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        Actor.log.warning("Page fetch failed for snapshot %s: %s", key, message)
        await push_data(
            {
                **result,
                "status": "error",
                "changed": False,
                "importance_score": 0,
                "change_summary": "Page could not be processed; previous snapshot preserved.",
                "previous_hash": previous["hash"] if previous else None,
                "current_hash": None,
                "similarity_ratio": None,
                "matched_concepts": [],
                "changes": [],
                "diff_truncated": False,
                "error": message,
            }
        )
        return False
    result.update(compare(previous["text"] if previous else None, current))
    await push_data(result)
    await store.set_value(
        key,
        {
            "schema_version": 1,
            "client_id": client_id,
            "url": url,
            "text": current,
            "hash": result["current_hash"],
            "updated_at": result["detected_at"],
        },
    )
    Actor.log.info(
        "Processed snapshot %s: %s (score %s)",
        key,
        result["status"],
        result["importance_score"],
    )
    return True


async def main() -> None:
    async with Actor:
        client_id, competitors = validate_input(await Actor.get_input())
        store = await Actor.open_key_value_store(name=STORE_NAME)
        succeeded = 0
        async with httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            for competitor in competitors:
                succeeded += await process_competitor(
                    client_id, competitor, store, client, Actor.push_data
                )
        Actor.log.info(
            "Monitoring complete: %s succeeded, %s failed",
            succeeded,
            len(competitors) - succeeded,
        )
        if not succeeded:
            await Actor.fail(status_message="All page fetches failed; see Dataset error records.")
