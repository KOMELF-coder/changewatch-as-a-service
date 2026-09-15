"""Apify Actor lifecycle."""

import httpx
from apify import Actor

from .alerts import validate_alert_options
from .monitor import USER_AGENT, validate_input
from .processing import process_competitor as process_competitor
from .weekly_report import record_activity

STORE_NAME = "changewatch-snapshots-v1"


async def main() -> None:
    async with Actor:
        data = await Actor.get_input()
        client_id, competitors = validate_input(data)
        alert_options = validate_alert_options(data)
        store = await Actor.open_key_value_store(name=STORE_NAME)
        succeeded = 0
        processed = 0

        async def publish(result):
            nonlocal processed
            processed += 1
            try:
                result.update(
                    await record_activity(
                        store,
                        client_id,
                        result,
                        alert_options,
                        evaluate=processed == len(competitors),
                    )
                )
                Actor.log.info(
                    "Weekly report: eligible=%s sent=%s error=%s",
                    result["weekly_report_eligible"],
                    result["weekly_report_sent"],
                    result["weekly_report_error"],
                )
            except Exception:
                result.update(
                    weekly_report_eligible=False,
                    weekly_report_sent=False,
                    weekly_report_error="Weekly activity state could not be updated; inspect storage.",
                )
                Actor.log.warning("Weekly activity state update failed.")
            await Actor.push_data(result)

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
                    client_id, competitor, store, client, publish, alert_options
                )
        Actor.log.info(
            "Monitoring complete: %s succeeded, %s failed",
            succeeded,
            len(competitors) - succeeded,
        )
        if not succeeded:
            await Actor.fail(status_message="All page fetches failed; see Dataset error records.")
