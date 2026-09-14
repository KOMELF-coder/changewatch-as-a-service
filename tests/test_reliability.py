import asyncio
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from src.alerts import render_alert, validate_alert_options
from src.ignore_rules import filter_html
from src.monitor import compare, extract_text, snapshot_key
from src.processing import process_competitor
from src.reliability import alert_decision, prepare_change, record_sent


class Store:
    def __init__(self):
        self.records = {}

    async def get_value(self, key):
        return copy.deepcopy(self.records.get(key))

    async def set_value(self, key, value):
        self.records[key] = copy.deepcopy(value)


async def sequence(pages, options=None, *, store=None, sender=None, operator_sender=None):
    store = store or Store()
    output = []
    sender = sender or AsyncMock(return_value=(True, None))

    async def push(item):
        output.append(item)

    for page in pages:

        def handler(request):
            return httpx.Response(
                page if isinstance(page, int) else 200,
                text=page if isinstance(page, str) else "",
                headers={"content-type": "text/html; charset=utf-8"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await process_competitor(
                "demo",
                {"name": "Shop", "url": "https://example.com/"},
                store,
                client,
                push,
                {"client_email": "client@example.com", "alert_threshold": 0, **(options or {})},
                sender,
                operator_sender,
            )
    return output, store, sender


@pytest.mark.parametrize(
    "pages,status,sends",
    [
        (["Alpha", "Beta"], "pending", 0),
        (["Alpha", "Beta", "Alpha"], "discarded", 0),
        (["Alpha", "Beta", "Beta"], "confirmed", 1),
        (["Alpha", "Beta", "Alpha", "Beta"], "pending", 0),
    ],
)
def test_confirmation_and_flapping(pages, status, sends):
    output, _, sender = asyncio.run(sequence(pages))
    assert output[-1]["confirmation_status"] == status
    assert sender.await_count == sends
    if len(pages) == 4:
        assert output[-1]["dynamic_content_detected"]
        assert output[-1]["alert_decision_reason"] == "dynamic_content"
        assert output[-1]["alert_suppressed_reason"] == "repeated_dynamic_state"


def test_superseded_pending_and_eventual_stable_state():
    output, _, sender = asyncio.run(sequence(["Alpha", "Beta", "Gamma", "Gamma"]))
    assert output[2]["pending_change_status"] == "superseded"
    assert output[-1]["confirmation_status"] == "confirmed"
    assert sender.await_count == 1


def test_flapping_does_not_permanently_hide_new_state():
    output, _, sender = asyncio.run(sequence(["Alpha", "Beta", "Alpha", "Beta", "Gamma", "Gamma"]))
    assert output[3]["dynamic_content_detected"]
    assert not output[-1]["dynamic_content_detected"]
    assert sender.await_count == 1


def test_duplicate_consecutive_state_sends_once():
    output, _, sender = asyncio.run(sequence(["Alpha", "Beta", "Beta", "Beta", "Beta"]))
    assert sender.await_count == 1
    assert output[-1]["alert_decision_reason"] == "unchanged"


@pytest.mark.parametrize("version", [1, 2])
def test_existing_snapshots_seed_baseline_without_reset(version):
    async def run():
        _, store, _ = await sequence(["Alpha"])
        key = snapshot_key("demo", "https://example.com/")
        store.records = {key: store.records[key]}
        store.records[key]["schema_version"] = version
        store.records[key].pop("ignore_signature")
        if version == 1:
            store.records[key].pop("entities")
        output, _, sender = await sequence(["Alpha", "Beta", "Beta"], store=store)
        assert output[0]["status"] == "unchanged"
        assert output[-1]["confirmation_status"] == "confirmed"
        assert sender.await_count == 1

    asyncio.run(run())


def test_ignore_configuration_change_initializes_without_alert():
    async def run():
        _, store, _ = await sequence(["Alpha"])
        output = AsyncMock()
        sender = AsyncMock()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text='<p>Alpha</p><div class="carousel">Beta</div>',
                    headers={"content-type": "text/html"},
                )
            )
        ) as client:
            await process_competitor(
                "demo",
                {"name": "Shop", "url": "https://example.com/", "ignore_selectors": [".carousel"]},
                store,
                client,
                output,
                sender=sender,
            )
        assert output.call_args.args[0]["status"] == "initialized"
        assert not output.call_args.args[0]["alert_triggered"]
        sender.assert_not_awaited()

    asyncio.run(run())


def test_storage_read_failure_notifies_operator(monkeypatch):
    monkeypatch.setenv("OPERATOR_EMAIL", "ops@example.com")
    store = Store()
    store.get_value = AsyncMock(side_effect=RuntimeError("private detail"))
    ops = AsyncMock(return_value=(True, None))
    output, _, _ = asyncio.run(sequence(["Alpha"], store=store, operator_sender=ops))
    assert output[0]["monitoring_status"] == "degraded"
    assert ops.await_count == 1
    assert "private detail" not in ops.call_args.args[2]


def product(price):
    return f'<article data-sku="A"><h2>Product A</h2>{price} EUR</article>'


def test_real_price_changes_immediate_and_distinct():
    output, _, sender = asyncio.run(sequence([product(99), product(79), product(69)]))
    assert sender.await_count == 2
    assert output[-1]["change_confidence"] == 100
    assert output[-1]["confirmation_status"] == "immediate"
    assert output[1]["alert_fingerprint"] != output[2]["alert_fingerprint"]


def test_failures_preserve_snapshot_and_recovery(monkeypatch):
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)

    async def run():
        _, store, _ = await sequence(["Alpha"])
        key = snapshot_key("demo", "https://example.com/")
        snapshot = copy.deepcopy(store.records[key])
        output, _, _ = await sequence([403, 403, 403], store=store)
        assert [r["monitoring_status"] for r in output] == ["degraded", "degraded", "failing"]
        assert store.records[key] == snapshot
        restored, _, _ = await sequence(["Alpha"], store=store)
        assert restored[0]["monitoring_status"] == "healthy"
        assert restored[0]["consecutive_failures"] == 0
        assert restored[0]["last_success_at"] and restored[0]["last_failure_at"]

    asyncio.run(run())


def test_operator_failure_and_recovery_once(monkeypatch):
    monkeypatch.setenv("OPERATOR_EMAIL", "ops@example.com")
    ops = AsyncMock(return_value=(True, None))
    output, _, sender = asyncio.run(
        sequence(["Alpha", 403, 403, 403, 403, "Alpha", "Alpha"], operator_sender=ops)
    )
    assert sender.await_count == 0
    assert ops.await_count == 2
    assert "Monitoring failure" in ops.call_args_list[0].args[1]
    assert "Monitoring restored" in ops.call_args_list[1].args[1]
    assert all(call.args[0] == "ops@example.com" for call in ops.call_args_list)


def test_unexpected_processing_notifies_operator(monkeypatch):
    monkeypatch.setenv("OPERATOR_EMAIL", "ops@example.com")
    monkeypatch.setattr("src.processing.fetch_text", AsyncMock(side_effect=RuntimeError("secret")))
    ops = AsyncMock(return_value=(True, None))
    output, _, _ = asyncio.run(sequence(["Alpha"], operator_sender=ops))
    assert ops.await_count == 1
    assert "secret" not in ops.call_args.args[2]
    assert output[0]["status"] == "error"


def test_confirmation_resets_after_fetch_failure():
    output, _, sender = asyncio.run(sequence(["Alpha", "Beta", 404, "Beta", "Beta"]))
    assert output[3]["confirmation_count"] == 1
    assert sender.await_count == 1


def test_selector_and_regex_filters(caplog):
    html = '<h1>Product A</h1><div class="carousel">15% promo</div><p>Updated 123 Price 99 EUR</p>'
    filtered = filter_html(html, [".carousel", "["], [r"Updated \d+", "["])
    assert extract_text(filtered) == "Product A Price 99 EUR"
    assert "invalid CSS" in caplog.text and "invalid text" in caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone", "Wrong/Zone"),
        ("confirmation_runs", 0),
        ("confirmation_runs", True),
        ("alert_cooldown_hours", -1),
    ],
)
def test_invalid_advanced_options(field, value):
    with pytest.raises(ValueError):
        validate_alert_options({field: value})


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"importance_score": 59}, "below_threshold"),
        ({"confirmation_status": "pending"}, "awaiting_confirmation"),
        ({"duplicate_alert_suppressed": True}, "duplicate"),
        ({"dynamic_content_detected": True}, "dynamic_content"),
        ({"change_confidence": 30}, "low_confidence"),
        ({}, "eligible"),
    ],
)
def test_decision(updates, reason):
    result = {
        "changed": True,
        "importance_score": 90,
        "change_confidence": 100,
        "change_type": "price_change",
        **updates,
    }
    assert (
        alert_decision(result, validate_alert_options({"client_email": "a@example.com"})) == reason
    )


def test_dedup_persisted_fingerprint_and_cooldown():
    options = validate_alert_options({"client_email": "a@example.com", "confirmation_runs": 1})
    old, new = {"text": "Alpha"}, {"text": "Beta"}
    state = {"baseline": old}
    now = datetime.now(timezone.utc)

    def candidate(at):
        return {
            **compare("Alpha", "Beta"),
            "client_id": "demo",
            "url": "https://example.com/",
            "detected_at": at.isoformat(),
        }

    first = candidate(now)
    prepare_change(first, state, new, options)
    first["alert_sent"] = True
    record_sent(first, state)
    again = candidate(now + timedelta(hours=1))
    prepare_change(again, state, new, options)
    assert again["duplicate_alert_suppressed"]
    later = candidate(now + timedelta(hours=25))
    prepare_change(later, state, new, options)
    assert not later["duplicate_alert_suppressed"]


@pytest.mark.parametrize(
    "language,zone,expected",
    [
        ("fr", "Europe/Paris", "14 septembre 2026 à 08:00 Europe/Paris"),
        ("en", "Europe/Paris", "September 14, 2026 at 08:00 Europe/Paris"),
        ("en", "UTC", "September 14, 2026 at 06:00 UTC"),
    ],
)
def test_client_timezone(language, zone, expected):
    result = {
        **compare("Prix 99 EUR", "Prix 79 EUR"),
        "competitor": "Shop",
        "url": "https://example.com/",
        "detected_at": "2026-09-14T06:00:00+00:00",
    }
    for html in (False, True):
        assert expected in render_alert(result, language, html=html, timezone=zone)[1]
