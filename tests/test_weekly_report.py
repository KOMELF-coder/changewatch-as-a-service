import asyncio
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.alerts import validate_alert_options
from src.weekly_report import fresh_period, record_activity, render_report


class Store:
    def __init__(self):
        self.records = {}

    async def get_value(self, key):
        return copy.deepcopy(self.records.get(key))

    async def set_value(self, key, value):
        self.records[key] = copy.deepcopy(value)


NOW = datetime(2026, 9, 14, 22, 0, tzinfo=timezone.utc)
OPTIONS = validate_alert_options({"client_email": "client@example.com", "language": "fr"})


def result(**updates):
    return {
        "url": "https://example.com/",
        "monitoring_status": "healthy",
        "alert_sent": False,
        **updates,
    }


def test_weekly_due_once_and_persisted():
    async def run():
        store, sender = Store(), AsyncMock(return_value=(True, None))
        for day in range(7):
            fields = await record_activity(
                store, "demo", result(), OPTIONS, now=NOW + timedelta(days=day), sender=sender
            )
            assert not fields["weekly_report_eligible"]
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7), sender=sender
        )
        assert fields["weekly_report_sent"]
        assert fields["weekly_report_checks_count"] == 7
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7, hours=1), sender=sender
        )
        assert not fields["weekly_report_sent"]
        assert sender.await_count == 1
        state = next(iter(store.records.values()))
        assert state["last_weekly_report_at"] and state["checks_count"] == 2
        assert state["healthy_urls"] == ["https://example.com/"]

    asyncio.run(run())


def test_alert_in_period_suppresses_report():
    async def run():
        store, sender = Store(), AsyncMock()
        await record_activity(
            store, "demo", result(alert_sent=True), OPTIONS, now=NOW, sender=sender
        )
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7), sender=sender
        )
        assert not fields["weekly_report_eligible"]
        sender.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,label", [("degraded", "Dégradées : 1"), ("failing", "En échec : 1")]
)
def test_health_and_escaped_urls(status, label):
    period = fresh_period(NOW)
    period["urls"] = {"https://example.com/?q=<unsafe>": {"status": status}}
    subject, text, html = render_report(period, OPTIONS)
    assert label in text and label in html
    assert "&lt;unsafe&gt;" in html and "<unsafe>" not in html
    assert "nécessitent une vérification" in text
    assert "Rapport de surveillance hebdomadaire" in subject


@pytest.mark.parametrize(
    "language,zone,expected",
    [
        ("fr", "Europe/Paris", "15/09/2026"),
        ("en", "America/New_York", "Sep 14, 2026"),
        ("en", "UTC", "Sep 14, 2026"),
    ],
)
def test_timezone(language, zone, expected):
    options = validate_alert_options({"language": language, "timezone": zone})
    _, text, html = render_report(fresh_period(NOW), options)
    assert expected in text and expected in html


@pytest.mark.parametrize(
    "failure", [(False, "Provider unavailable"), RuntimeError("private detail")]
)
def test_email_failure_retries_without_crashing(failure):
    async def run():
        store = Store()
        sender = (
            AsyncMock(side_effect=failure)
            if isinstance(failure, Exception)
            else AsyncMock(return_value=failure)
        )
        await record_activity(store, "demo", result(), OPTIONS, now=NOW, sender=sender)
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7), sender=sender
        )
        assert fields["weekly_report_eligible"] and fields["weekly_report_error"]
        assert "private detail" not in fields["weekly_report_error"]
        assert next(iter(store.records.values()))["pending"]
        sender = AsyncMock(return_value=(True, None))
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7, hours=1), sender=sender
        )
        assert fields["weekly_report_sent"]
        assert not next(iter(store.records.values()))["pending"]

    asyncio.run(run())


def test_missing_recipient_preserves_eligibility_for_later():
    async def run():
        store, sender = Store(), AsyncMock(return_value=(True, None))
        options = validate_alert_options({})
        await record_activity(store, "demo", result(), options, now=NOW, sender=sender)
        fields = await record_activity(
            store, "demo", result(), options, now=NOW + timedelta(days=7), sender=sender
        )
        assert not fields["weekly_report_eligible"]
        sender.assert_not_awaited()
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7, hours=1), sender=sender
        )
        assert fields["weekly_report_sent"]

    asyncio.run(run())


def test_report_waits_for_last_url_in_run():
    async def run():
        store, sender = Store(), AsyncMock(return_value=(True, None))
        await record_activity(store, "demo", result(), OPTIONS, now=NOW, sender=sender)
        fields = await record_activity(
            store,
            "demo",
            result(),
            OPTIONS,
            evaluate=False,
            now=NOW + timedelta(days=7),
            sender=sender,
        )
        assert not fields["weekly_report_eligible"]
        fields = await record_activity(
            store,
            "demo",
            result(url="https://example.com/two"),
            OPTIONS,
            now=NOW + timedelta(days=7),
            sender=sender,
        )
        assert fields["weekly_report_sent"] and sender.await_count == 1

    asyncio.run(run())


def test_five_pages_seven_days_count_35_checks():
    async def run():
        store, sender = Store(), AsyncMock(return_value=(True, None))
        for day in range(8):
            for url in range(5):
                fields = await record_activity(
                    store,
                    "demo",
                    result(url=f"https://example.com/{url}"),
                    OPTIONS,
                    now=NOW + timedelta(days=day),
                    evaluate=url == 4,
                    sender=sender,
                )
        assert fields["weekly_report_checks_count"] == 35
        assert sender.await_count == 1
        assert "5 pages surveillées" in sender.call_args.args[2]

    asyncio.run(run())


def test_late_retry_does_not_allow_daily_reports():
    async def run():
        store = Store()
        failure = AsyncMock(return_value=(False, "Unavailable"))
        await record_activity(store, "demo", result(), OPTIONS, now=NOW, sender=failure)
        await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7), sender=failure
        )
        success = AsyncMock(return_value=(True, None))
        await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=13), sender=success
        )
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=14), sender=success
        )
        assert not fields["weekly_report_sent"] and success.await_count == 1

    asyncio.run(run())


def test_unsent_significant_alert_is_not_reported_as_no_change():
    period = fresh_period(NOW)
    period["urls"] = {"https://example.com/": {"status": "healthy"}}
    period["unsent_qualifying_alerts"] = 1
    _, text, _ = render_report(period, OPTIONS)
    assert "n'ont pas pu être envoyées" in text
    assert "0 alerte significative envoyée" in text


def test_uncertain_delivery_requires_review_after_idempotency_window():
    async def run():
        store = Store()
        sender = AsyncMock(side_effect=RuntimeError("unknown acceptance"))
        await record_activity(store, "demo", result(), OPTIONS, now=NOW, sender=sender)
        await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7), sender=sender
        )
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=8), sender=sender
        )
        assert not fields["weekly_report_eligible"]
        assert "operator review" in fields["weekly_report_error"]
        assert sender.await_count == 1

    asyncio.run(run())


def test_default_transport_uses_period_specific_idempotency_key(monkeypatch):
    async def run():
        store = Store()
        transport = AsyncMock(return_value=(True, None))
        monkeypatch.setattr("src.weekly_report.send_email", transport)
        await record_activity(store, "demo", result(), OPTIONS, now=NOW)
        fields = await record_activity(
            store, "demo", result(), OPTIONS, now=NOW + timedelta(days=7)
        )
        assert fields["weekly_report_sent"]
        assert transport.call_args.kwargs["idempotency_key"].startswith("weekly-")
        assert '<html lang="fr">' in transport.call_args.args[3]

    asyncio.run(run())
