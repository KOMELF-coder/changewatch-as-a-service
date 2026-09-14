"""Per-URL monitoring health and separate, rate-limited operator notifications."""

import logging
import os
from datetime import datetime
from urllib.parse import urlsplit

from .email_delivery import send_email, valid_email

log = logging.getLogger(__name__)


def update_health(state: dict, now: str, error: str | None = None) -> dict:
    health = state.setdefault(
        "health",
        {
            "consecutive_failures": 0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": None,
            "monitoring_status": "healthy",
        },
    )
    if error:
        health["consecutive_failures"] += 1
        health.update(
            last_failure_at=now,
            last_error=error,
            monitoring_status="failing" if health["consecutive_failures"] >= 3 else "degraded",
        )
        if state.get("pending"):
            state["pending"]["count"] = 0
    else:
        health.update(
            consecutive_failures=0,
            last_success_at=now,
            last_error=None,
            monitoring_status="healthy",
        )
    return dict(health)


async def notify_operator(
    state: dict, client_id: str, url: str, now: str, *, unexpected=False, sender=None
) -> None:
    recipient = os.getenv("OPERATOR_EMAIL", "").strip()
    if not recipient:
        return
    if not valid_email(recipient):
        log.warning("Operator notifications disabled: invalid OPERATOR_EMAIL.")
        return
    health = state["health"]
    notified = state.get("ops_failure_notified", False)
    failure = (health["monitoring_status"] == "failing" or unexpected) and not notified
    recovery = health["monitoring_status"] == "healthy" and notified
    if not failure and not recovery:
        return
    kind = "restored" if recovery else "failure"
    timestamp = datetime.fromisoformat(now).timestamp()
    attempts = state.setdefault("ops_attempts", {})
    if timestamp - attempts.get(kind, 0) < 3600:
        return
    attempts[kind] = timestamp
    subject = f"[ChangeWatch Ops] Monitoring {kind} - {' '.join(client_id.split())} - {urlsplit(url).hostname}"
    body = f"Internal monitoring notification\nClient: {client_id}\nURL: {url}\nStatus: {health['monitoring_status']}\nConsecutive failures: {health['consecutive_failures']}\nError: {health['last_error'] or 'none'}\nDetected at: {now}"
    try:
        sent, _ = await (sender or send_email)(recipient, subject, body, "")
    except Exception:
        sent = False
    if sent:
        state["ops_failure_notified"] = not recovery
        if recovery:
            state["ops_attempts"] = {}
    log.info("Operator notification: type=%s accepted=%s", kind, sent)
