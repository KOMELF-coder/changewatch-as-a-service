import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from src.alerts import apply_alert, render_alert
from src.email_delivery import send_email
from src.main import process_competitor
from src.monitor import compare, validate_input


def result(old="Prix : 99 €", new="Prix : 79 €"):
    return {
        **compare(old, new),
        "client_id": "demo",
        "competitor": "Test Shop",
        "url": "https://example.com",
        "detected_at": "2026-09-11T12:00:00+00:00",
    }


@pytest.mark.parametrize("score,triggered", [(59, False), (60, True), (61, True)])
def test_threshold(score, triggered):
    alert = result()
    alert["importance_score"] = score
    sender = AsyncMock(return_value=(True, None))
    asyncio.run(apply_alert(alert, {"client_email": "client@example.com"}, sender))
    assert alert["alert_triggered"] is triggered
    assert alert["alert_sent"] is triggered
    assert bool(alert["alert_body"]) is triggered
    assert sender.await_count == int(triggered)
    assert alert["alert_threshold"] == 60


@pytest.mark.parametrize("old,new", [(None, "99 €"), ("99 €", "99 €")])
def test_no_change_never_triggers(old, new):
    alert = result(old, new)
    sender = AsyncMock()
    asyncio.run(apply_alert(alert, {"alert_threshold": 0}, sender))
    assert not alert["alert_triggered"]
    sender.assert_not_awaited()


def test_missing_email():
    alert = result()
    sender = AsyncMock()
    asyncio.run(apply_alert(alert, sender=sender))
    assert alert["alert_triggered"] and alert["alert_subject"]
    assert not alert["alert_sent"] and alert["email_error"] is None
    sender.assert_not_awaited()


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("EMAIL_PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM_ADDRESS", raising=False)
    alert = result()
    asyncio.run(apply_alert(alert, {"client_email": "client@example.com"}))
    assert not alert["alert_sent"]
    assert "disabled" in alert["email_error"]
    assert alert["alert_body"]


def test_french_price_decrease():
    subject, body = render_alert(result(), "fr")
    assert subject == "[ChangeWatch] Test Shop - Variation de prix"
    assert "Test Shop a baissé son prix de 99 € à 79 €." in body
    assert "Variation : -20 € (-20,2 %)" in body
    assert "Baisse de prix" in body
    assert "Action recommandée" in body


def test_english_price_increase():
    subject, body = render_alert(result("$79", "$99"), "en")
    assert subject == "[ChangeWatch] Test Shop - Price change"
    assert "Test Shop increased its price from 79 USD to 99 USD." in body
    assert "Price increase" in body and "Difference: 20 USD" in body


@pytest.mark.parametrize(
    "concept,title",
    [
        ("promotion", "Discount/promotion"),
        ("new offering", "Product/service"),
        ("shipping", "Shipping/delivery"),
        ("availability", "Availability/stock"),
        ("feature", "Important content"),
    ],
)
@pytest.mark.parametrize("language", ["en", "fr"])
def test_commercial_templates(concept, title, language):
    alert = result("Old content", "New content")
    alert["matched_concepts"] = [concept]
    subject, body = render_alert(alert, language)
    assert ("Action recommandée" if language == "fr" else "Recommended action") in body
    assert "New" in body
    if language == "en":
        assert title in body


@pytest.mark.parametrize(
    "field,value",
    [
        ("alert_threshold", -1),
        ("alert_threshold", 101),
        ("alert_threshold", True),
        ("alert_threshold", 60.5),
        ("language", "de"),
        ("client_email", "bad"),
        ("client_email", "a@example.com\nb@example.com"),
    ],
)
def test_invalid_options(field, value):
    data = {
        "client_id": "demo",
        "competitors": [{"name": "A", "url": "https://example.com"}],
        field: value,
    }
    with pytest.raises(ValueError):
        validate_input(data)


@pytest.mark.parametrize(
    "status,payload,sent",
    [
        (200, {"id": "message-123"}, True),
        (401, {"message": "sensitive details"}, False),
        (429, {}, False),
        (500, {}, False),
        (200, {}, False),
    ],
)
def test_resend_transport(monkeypatch, status, payload, sent):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "test-secret")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "alerts@example.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "ChangeWatch")
    factory = httpx.AsyncClient

    def handler(request):
        assert request.url == "https://api.resend.com/emails"
        assert request.headers["authorization"] == "Bearer test-secret"
        assert json.loads(request.content) == {
            "from": "ChangeWatch <alerts@example.com>",
            "to": ["client@example.com"],
            "subject": "Subject",
            "text": "Body",
        }
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        "src.email_delivery.httpx.AsyncClient",
        lambda **kw: factory(transport=httpx.MockTransport(handler), **kw),
    )
    accepted, error = asyncio.run(send_email("client@example.com", "Subject", "Body"))
    assert accepted is sent
    assert "test-secret" not in (error or "") and "sensitive details" not in (error or "")


def test_email_failure_does_not_crash_monitor(caplog):
    async def run():
        store = AsyncMock()
        store.get_value.return_value = None
        output = AsyncMock()
        sender = AsyncMock(side_effect=RuntimeError("DO NOT LOG SECRET"))
        entry = {"name": "Test Shop", "url": "https://example.com"}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text="<p>Prix : 99 €</p>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
        ) as client:
            await process_competitor("demo", entry, store, client, output)
            store.get_value.return_value = store.set_value.call_args.args[1]
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text="<p>Prix : 79 €</p>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            )
        ) as client:
            assert await process_competitor(
                "demo",
                entry,
                store,
                client,
                output,
                {"client_email": "client@example.com", "language": "fr"},
                sender,
            )
        alert = output.call_args.args[0]
        assert alert["alert_triggered"] and not alert["alert_sent"]
        assert "DO NOT LOG SECRET" not in alert["email_error"]
        assert "79 €" in alert["alert_body"]
        assert store.set_value.call_args.args[1]["text"] == "Prix : 79 €"

    asyncio.run(run())
    assert "DO NOT LOG SECRET" not in caplog.text


@pytest.mark.parametrize("mode", ["timeout", "invalid-json"])
def test_unconfirmed_delivery(monkeypatch, mode):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "test-secret")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "alerts@example.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "ChangeWatch")
    factory = httpx.AsyncClient

    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("test-secret")
        return httpx.Response(200, text="not-json test-secret")

    monkeypatch.setattr(
        "src.email_delivery.httpx.AsyncClient",
        lambda **kw: factory(transport=httpx.MockTransport(handler), **kw),
    )
    accepted, error = asyncio.run(send_email("client@example.com", "Subject", "Body"))
    assert not accepted
    assert "unconfirmed" in error
    assert "test-secret" not in error


def test_new_input_fields_accepted():
    data = {
        "client_id": "demo",
        "competitors": [{"name": "A", "url": "https://example.com"}],
        "client_email": "client@example.com",
        "language": "fr",
        "alert_threshold": 80,
    }
    assert validate_input(data)[0] == "demo"
