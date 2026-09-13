import pytest

from src.monitor import compare


@pytest.mark.parametrize(
    "before,after,old,new,currency,difference,percent,floor",
    [
        ("Prix : 99 €", "Prix : 79 €", 99, 79, "EUR", -20, -20.2, 90),
        ("$99", "$89", 99, 89, "USD", -10, -10.1, 80),
        ("99,99 €", "89,99 €", 99.99, 89.99, "EUR", -10, -10, 80),
        ("€99", "€109", 99, 109, "EUR", 10, 10.1, 80),
        ("99 USD", "79 USD", 99, 79, "USD", -20, -20.2, 90),
        ("£99", "£98", 99, 98, "GBP", -1, -1.01, 60),
        ("100 €", "105 €", 100, 105, "EUR", 5, 5, 70),
        ("100 €", "110 €", 100, 110, "EUR", 10, 10, 80),
        ("100 €", "120 €", 100, 120, "EUR", 20, 20, 90),
        ("100 €", "80 €", 100, 80, "EUR", -20, -20, 90),
        ("0 €", "10 €", 0, 10, "EUR", 10, None, 60),
        ("10 €", "0 €", 10, 0, "EUR", -10, -100, 90),
        ("1 299,99 €", "1 199,99 €", 1299.99, 1199.99, "EUR", -100, -7.69, 70),
        ("$1,299.99", "$1,199.99", 1299.99, 1199.99, "USD", -100, -7.69, 70),
    ],
)
def test_price_changes(before, after, old, new, currency, difference, percent, floor):
    result = compare(before, after)
    assert result["changed"]
    assert result["change_type"] == "price_change"
    assert result["old_price"] == old
    assert result["new_price"] == new
    assert result["currency"] == currency
    assert result["price_change_absolute"] == difference
    assert result["price_change_percent"] == percent
    assert floor <= result["importance_score"] <= 100


def test_production_regression():
    assert compare("Prix : 99 €", "Prix : 79 €")["importance_score"] == 90


@pytest.mark.parametrize(
    "before,after",
    [
        ("Prix : 99 €", "Prix : 99 €"),
        ("Prix : 99 € blue", "Prix : 99 € green"),
        ("99,99 €", "€99.99"),
        ("Available", "Available 99 €"),
        ("Available 99 €", "Available"),
        ("99 €", "79 USD"),
        ("99 € 49 €", "79 € 39 €"),
    ],
)
def test_no_price_pair(before, after):
    assert compare(before, after).get("change_type") != "price_change"


def test_first_run_is_not_price_change():
    result = compare(None, "Prix : 99 €")
    assert result["importance_score"] == 0
    assert "old_price" not in result


def test_separate_prices_and_primary():
    result = compare("Basic 100 € Premium 200 €", "Basic 99 € Premium 150 €")
    assert len(result["price_changes"]) == 2
    assert result["old_price"] == 200
    assert result["importance_score"] >= 90


@pytest.mark.parametrize(
    "keyword,concept",
    [
        ("price", "pricing"),
        ("pricing", "pricing"),
        ("cost", "pricing"),
        ("tarif", "pricing"),
        ("tarifs", "pricing"),
        ("prix", "pricing"),
        ("coût", "pricing"),
        ("discount", "promotion"),
        ("sale", "promotion"),
        ("promotion", "promotion"),
        ("promo", "promotion"),
        ("réduction", "promotion"),
        ("remise", "promotion"),
        ("soldes", "promotion"),
        ("product", "new offering"),
        ("service", "new offering"),
        ("produit", "new offering"),
        ("produits", "new offering"),
        ("nouveau produit", "new offering"),
        ("nouveau service", "new offering"),
        ("shipping", "shipping"),
        ("delivery", "shipping"),
        ("livraison", "shipping"),
        ("frais de port", "shipping"),
        ("subscription", "subscription"),
        ("monthly", "subscription"),
        ("annual", "subscription"),
        ("abonnement", "subscription"),
        ("mensuel", "subscription"),
        ("annuel", "subscription"),
        ("availability", "availability"),
        ("stock", "availability"),
        ("available", "availability"),
        ("unavailable", "availability"),
        ("disponibilité", "availability"),
        ("disponible", "availability"),
        ("indisponible", "availability"),
        ("rupture", "availability"),
        ("launch", "launch"),
        ("launched", "launch"),
        ("lancement", "launch"),
        ("nouveauté", "launch"),
    ],
)
def test_multilingual_keywords(keyword, concept):
    assert concept in compare(f"{keyword} A", f"{keyword} B")["matched_concepts"]
