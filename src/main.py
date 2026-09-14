"""Apify Actor lifecycle."""

import httpx
from apify import Actor

from .alerts import validate_alert_options
from .monitor import USER_AGENT, validate_input
from .processing import process_competitor as process_competitor

STORE_NAME = "changewatch-snapshots-v1"


async def main() -> None:
    async with Actor:
        data = await Actor.get_input()
        client_id, competitors = validate_input(data)
        alert_options = validate_alert_options(data)
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
                    client_id, competitor, store, client, Actor.push_data, alert_options
                )
        Actor.log.info(
            "Monitoring complete: %s succeeded, %s failed",
            succeeded,
            len(competitors) - succeeded,
        )
        if not succeeded:
            await Actor.fail(status_message="All page fetches failed; see Dataset error records.")
