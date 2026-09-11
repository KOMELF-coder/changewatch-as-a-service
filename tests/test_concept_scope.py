import pytest

from src.monitor import compare


@pytest.mark.parametrize(
    "before,after",
    [
        ("Prix : 99 €", "Prix : 79 €"),
        ("99 €", "79 €"),
        (
            "Produit Livraison Prix : 99 € Stock Promotion",
            "Produit Livraison Prix : 79 € Stock Promotion",
        ),
        (
            "Prix : 99 € Livraison gratuite Nouveau produit disponible",
            "Prix : 79 € Livraison gratuite Nouveau produit disponible",
        ),
    ],
)
def test_pure_price(before, after):
    result = compare(before, after)
    assert result["matched_concepts"] == ["pricing"]
    assert result["change_type"] == "price_change"
    assert result["old_price"] == 99 and result["new_price"] == 79
    assert result["importance_score"] >= 90
    assert "concepts: pricing." in result["change_summary"]


def test_exact_summary():
    assert (
        compare("Prix : 99 €", "Prix : 79 €")["change_summary"]
        == "1 text change(s); concepts: pricing. Before: 99. After: 79."
    )


def test_shipping_only():
    result = compare(
        "Produit Prix 99 € Livraison gratuite Stock disponible",
        "Produit Prix 99 € Livraison payante Stock disponible",
    )
    assert result["matched_concepts"] == ["shipping"]


def test_shipping_fee_added():
    assert compare("Livraison gratuite", "Livraison 4,99 €")["matched_concepts"] == ["shipping"]


def test_product_wording():
    assert compare("Découvrez notre produit classique", "Découvrez notre nouveau produit")[
        "matched_concepts"
    ] == ["new offering"]


def test_combined_sections():
    result = compare("Prix : 99 € Livraison gratuite", "Prix : 79 € Livraison 4,99 €")
    assert result["matched_concepts"] == ["pricing", "shipping"]
    assert result["importance_score"] >= 90


def test_unchanged():
    assert (
        compare("Prix 99 € Livraison gratuite Produit", "Prix 99 € Livraison gratuite Produit")[
            "matched_concepts"
        ]
        == []
    )


def test_nearby_unchanged_keywords_do_not_leak():
    assert (
        compare(
            "Produit disponible Couleur bleue Livraison gratuite",
            "Produit disponible Couleur rouge Livraison gratuite",
        )["matched_concepts"]
        == []
    )


def test_changed_keywords_are_combined_without_cross_fragment_phrases():
    result = compare(
        "Livraison gratuite. Article ancien. Promo terminée.",
        "Livraison payante. Article ancien. Promo active.",
    )
    assert result["matched_concepts"] == ["promotion", "shipping"]
