import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from bs4 import BeautifulSoup

from src.alerts import apply_alert, render_alert
from src.email_delivery import send_email
from src.html_email import render_html
from src.monitor import compare


def alert(old="99 €", new="79 €"):
    return {
        **compare(old, new),
        "competitor": "Test Shop",
        "url": "https://example.com/pricing?a=1&b=2",
        "detected_at": "2026-09-11T12:00:00+00:00",
    }


@pytest.mark.parametrize(
    "language,old,new,badge,cta,footer",
    [
        (
            "fr",
            "99 €",
            "79 €",
            "Baisse de prix",
            "Voir la page concurrente",
            "Surveillance automatisée par ChangeWatch",
        ),
        (
            "fr",
            "79 €",
            "99 €",
            "Hausse de prix",
            "Voir la page concurrente",
            "Surveillance automatisée par ChangeWatch",
        ),
        (
            "en",
            "99 €",
            "79 €",
            "Price decrease",
            "View competitor page",
            "Automated monitoring by ChangeWatch",
        ),
    ],
)
def test_localized_html(language, old, new, badge, cta, footer):
    result = alert(old, new)
    _, html = render_alert(result, language, html=True)
    soup = BeautifulSoup(html, "html.parser")
    assert soup.html["lang"] == language
    assert badge in soup.get_text()
    assert footer in soup.get_text()
    assert soup.a.get_text() == cta
    assert soup.a["href"] == result["url"]
    assert soup.find("meta", attrs={"name": "viewport"})
    assert not soup.find_all(["style", "script", "link", "img"])
    assert "max-width:600px" in html
    if language == "fr" and old == "99 €":
        assert "-20 € (-20,2 %)" in soup.get_text()


def test_dynamic_content_escaped():
    result = alert()
    result["competitor"] = '<img src=x onerror="bad()"> & Shop'
    result["url"] = 'https://example.com/?q=" onclick="bad()&x=<tag>'
    result["detected_at"] = "<script>bad()</script>"
    html = render_html(
        result,
        "en",
        "<b>badge</b>",
        ["Before: <svg onload=bad()>"],
        "<script>summary</script>",
        "<img src=action>",
    )
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.find_all(["script", "img", "svg", "b"])
    assert set(soup.a.attrs) == {"href", "style"}
    assert soup.a["href"] == result["url"]
    assert result["competitor"] in soup.get_text()
    assert "<script>summary</script>" in soup.get_text()
    assert "<img src=action>" in soup.get_text()
    assert "&lt;" in html and "&quot;" in html and "&amp;" in html


def test_non_price_excerpts_escaped():
    result = alert("Old text", "<img src=x>")
    _, html = render_alert(result, "en", html=True)
    assert "<img" not in html
    assert "&lt;img" in html


def test_unsafe_scheme_is_not_linked():
    result = alert()
    result["url"] = "javascript:alert(1)"
    _, html = render_alert(result, "en", html=True)
    assert BeautifulSoup(html, "html.parser").a is None


def test_plain_text_preserved_and_both_bodies_sent():
    result = alert()
    expected_subject, expected_text = render_alert(result, "fr")
    sender = AsyncMock(return_value=(True, None))
    asyncio.run(
        apply_alert(result, {"client_email": "client@example.com", "language": "fr"}, sender)
    )
    assert result["alert_body"] == expected_text
    assert "Test Shop a baissé son prix de 99 € à 79 €." in expected_text
    assert "<table" not in expected_text
    assert result["alert_html"].startswith("<!doctype html>")
    sender.assert_awaited_once_with(
        "client@example.com", expected_subject, expected_text, result["alert_html"]
    )


def test_no_alert_has_empty_html():
    result = alert("99 €", "99 €")
    asyncio.run(apply_alert(result))
    assert result["alert_html"] == ""
    assert result["alert_body"] == ""


def test_resend_receives_both_formats(monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER_API_KEY", "test-secret")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "alerts@example.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "ChangeWatch")
    factory = httpx.AsyncClient

    def handler(request):
        data = json.loads(request.content)
        assert data["text"] == "Plain fallback"
        assert data["html"] == "<p>HTML body</p>"
        return httpx.Response(200, json={"id": "test-id"})

    monkeypatch.setattr(
        "src.email_delivery.httpx.AsyncClient",
        lambda **kw: factory(transport=httpx.MockTransport(handler), **kw),
    )
    assert asyncio.run(
        send_email("client@example.com", "Subject", "Plain fallback", "<p>HTML body</p>")
    ) == (True, None)
