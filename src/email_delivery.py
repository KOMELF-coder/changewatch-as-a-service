"""Optional Resend transport. Credentials never enter scrape requests or results."""

import asyncio
import os
import re

import httpx


def valid_email(value: str) -> bool:
    return (
        len(value) <= 254
        and re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", value) is not None
    )


async def send_email(
    recipient: str, subject: str, body: str, html: str = ""
) -> tuple[bool, str | None]:
    key = os.getenv("EMAIL_PROVIDER_API_KEY", "").strip()
    address = os.getenv("EMAIL_FROM_ADDRESS", "").strip()
    name = os.getenv("EMAIL_FROM_NAME", "ChangeWatch").strip() or "ChangeWatch"
    if not key or not address:
        return False, "Email delivery disabled: missing provider credentials or sender address."
    if not valid_email(address) or any(c in name for c in '\r\n<>"'):
        return False, "Email delivery disabled: invalid sender configuration."
    try:
        # Dedicated client: no bearer token can reach a competitor or redirect target.
        async with asyncio.timeout(25):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20, connect=10), follow_redirects=False
            ) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "from": f"{name} <{address}>",
                        "to": [recipient],
                        "subject": subject,
                        "text": body,
                        **({"html": html} if html else {}),
                    },
                )
        if not 200 <= response.status_code < 300:
            return False, f"Resend rejected email (HTTP {response.status_code})."
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("id"), str)
            or not payload["id"]
        ):
            return False, "Resend returned an invalid acknowledgement."
        return True, None
    except (httpx.HTTPError, TimeoutError, ValueError):
        # Provider bodies and exception strings can contain credentials or recipient data.
        return (
            False,
            "Email request failed or acknowledgement was unreadable; delivery is unconfirmed.",
        )
