import copy

import pytest
from bs4 import BeautifulSoup

from src.alerts import render_alert
from src.email_presentation import display_time

PRICE = {
    "change_type": "price_change",
    "entity_name": "Produit Test",
    "old_price": 99,
    "new_price": 79,
    "currency": "EUR",
    "price_change_absolute": -20,
    "price_change_percent": -20.2,
}
REMOVED = {"change_type": "product_removed", "entity_name": "Nouveau Produit"}
NEW = {"change_type": "new_product", "entity_name": "Desk", "price": 49, "currency": "EUR"}


def result(events):
    return {
        "competitor": "Test Shop",
        "url": "https://example.com/?a=1&b=2",
        "importance_score": 100,
        "detected_at": "2026-09-13T15:05:00+02:00",
        "entity_changes": events,
        **events[0],
    }


@pytest.mark.parametrize(
    "events",
    [
        [PRICE],
        [PRICE, REMOVED],
        [NEW, REMOVED],
        [
            PRICE,
            NEW,
            REMOVED,
            {
                "change_type": "shipping",
                "entity_name": "Delivery",
                "before": "Free",
                "after": "4.99 EUR",
            },
            {
                "change_type": "availability",
                "entity_name": "Chair",
                "before": "In stock",
                "after": "Unavailable",
            },
            {"change_type": "promotion", "entity_name": "Offer", "before": "10%", "after": "20%"},
            {
                "change_type": "generic_content_change",
                "entity_name": "Details",
                "before": "Old",
                "after": "New",
            },
        ],
    ],
)
@pytest.mark.parametrize("language", ["fr", "en"])
def test_all_events_have_cards_and_plain_text(events, language):
    data = result(events)
    original = copy.deepcopy(data)
    subject, html = render_alert(data, language, html=True)
    plain_subject, plain = render_alert(data, language)
    assert data == original
    assert subject == plain_subject
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("[data-event-card]")
    assert len(cards) == len(events)
    for card, event in zip(cards, events, strict=True):
        assert event["entity_name"] in card.get_text()
        assert event["entity_name"] in plain
    if len(events) > 1:
        assert subject == f"[ChangeWatch] Test Shop - {len(events)} " + (
            "changements détectés" if language == "fr" else "changes detected"
        )
    assert "100/100" in html


def test_summary_and_action_cover_price_and_removal():
    _, plain = render_alert(result([PRICE, REMOVED]), "fr")
    summary = plain.split("Résumé :\n")[1].split("\n\n")[0]
    assert "Produit Test a baissé de 99 € à 79 €." in summary
    assert "Nouveau Produit a été retiré de la page." in summary
    action = plain.split("Action recommandée :\n")[1].split("\n\n")[0]
    assert "temporaire" in action and "retrait" in action and "rupture de stock" in action


def test_addition_removal_recommendations():
    _, plain = render_alert(result([NEW, REMOVED]), "en")
    assert "Review the new product" in plain
    assert "product removal" in plain
    assert "49 EUR" in plain


def test_more_than_ten_events_are_not_truncated():
    events = [{"change_type": "new_product", "entity_name": f"Product {i}"} for i in range(12)]
    _, html = render_alert(result(events), "en", html=True)
    assert len(BeautifulSoup(html, "html.parser").select("[data-event-card]")) == 12


def test_no_primary_identity_leaks_into_other_events():
    data = result([PRICE, {"change_type": "product_removed", "entity_id": "other-id"}])
    _, html = render_alert(data, "en", html=True)
    cards = BeautifulSoup(html, "html.parser").select("[data-event-card]")
    assert "Produit Test" not in cards[1].get_text()
    assert "other-id" in cards[1].get_text()


def test_event_text_is_escaped():
    data = result(
        [
            PRICE,
            {
                "change_type": "shipping",
                "entity_name": "<img src=x>",
                "before": "<script>bad()</script>",
                "after": "<svg onload=bad()>",
            },
        ]
    )
    _, html = render_alert(data, "en", html=True)
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.find_all(["script", "img", "svg"])
    assert "<svg onload=bad()>" in soup.get_text()
    assert soup.a["href"] == data["url"]


def test_localized_timestamp_preserves_zone():
    value = "2026-09-13T15:05:00+02:00"
    assert display_time(value, True) == "13 septembre 2026 à 15:05 Europe/Paris"
    assert display_time(value, False) == "September 13, 2026 at 15:05 Europe/Paris"
    assert display_time("2026-09-13T13:05:00Z", True, "UTC").endswith("13:05 UTC")
    assert display_time("unknown", True) == "unknown"
