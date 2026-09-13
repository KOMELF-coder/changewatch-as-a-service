import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src.alerts import render_alert
from src.entities import extract_entities
from src.main import process_competitor
from src.monitor import compare, extract_text, text_hash

URL = "https://shop.example/category"


def card(name, price, sku=None, path=None, extra=""):
    identifier = f' data-sku="{sku}"' if sku else ""
    link = f'<a href="{path}">{name}</a>' if path else name
    return f'<article class="product-card"{identifier}><h2>{link}</h2><p>{price} EUR</p>{extra}</article>'


def comparison(before, after):
    return compare(
        extract_text(before),
        extract_text(after),
        previous_entities=extract_entities(before, URL),
        current_entities=extract_entities(after, URL),
        require_entity_match=True,
    )


def test_same_product_price():
    result = comparison(
        card("Product A", "14.99", "A", "/product/a"), card("Product A", "12.99", "A", "/product/a")
    )
    assert result["change_type"] == "price_change"
    assert result["entity_id"] == "A"
    assert result["entity_name"] == "Product A"
    assert result["entity_url"] == "https://shop.example/product/a"
    assert result["old_price"] == 14.99 and result["new_price"] == 12.99
    assert result["importance_score"] >= 80


def test_ikea_category_replacement_regression():
    # Synthetic reproduction of the reported IKEA category failure; not captured site HTML.
    before = card("Product A", "14.99", "A") + card("Product B", "29.99", "B")
    after = card("Product B", "29.99", "B") + card("Product C", "2.99", "C")
    result = comparison(before, after)
    assert result["change_type"] != "price_change"
    assert "old_price" not in result and "price_changes" not in result
    assert [(e["change_type"], e["entity_name"]) for e in result["entity_changes"]] == [
        ("product_removed", "Product A"),
        ("new_product", "Product C"),
    ]
    assert result["importance_score"] < 80


def test_text_only_replacement_never_pairs_unrelated_products():
    result = compare(
        "Product A - 14.99 EUR Product B - 29.99 EUR", "Product B - 29.99 EUR Product C - 2.99 EUR"
    )
    assert result["change_type"] != "price_change"


@pytest.mark.parametrize("kind", ["new_product", "product_removed"])
def test_addition_removal(kind):
    one = card("Product B", "29.99", "B")
    two = one + card("Product C", "2.99", "C")
    result = comparison(one, two) if kind == "new_product" else comparison(two, one)
    assert result["change_type"] == kind
    assert result["entity_name"] == "Product C"
    assert len(result["entity_changes"]) == 1
    subject, body = render_alert(
        {**result, "competitor": "IKEA", "url": URL, "detected_at": "today"}, "en"
    )
    assert "Product C" in body
    assert "Price change" not in subject


def test_one_real_change_among_neighbors():
    before = card("Product A", "14.99", "A") + card("Product B", "29.99", "B")
    after = card("Product B", "29.99", "B") + card("Product A", "12.99", "A")
    result = comparison(before, after)
    assert len(result["entity_changes"]) == len(result["price_changes"]) == 1
    assert result["entity_name"] == "Product A"


def test_reordering_is_not_price_change():
    a, b = card("Product A", "14.99", "A"), card("Product B", "29.99", "B")
    result = comparison(a + b, b + a)
    assert result["entity_changes"] == []
    assert result["change_type"] == "generic_content_change"


def test_duplicate_identity_is_ambiguous():
    result = comparison(
        card("Product", "14.99", "same") + card("Product", "29.99", "same"),
        card("Product", "2.99", "same"),
    )
    assert result["entity_changes"] == []
    assert result["change_type"] == "generic_content_change"


def test_different_sku_never_falls_back_to_same_title():
    result = comparison(card("Product", "14.99", "A"), card("Product", "2.99", "C"))
    assert result["change_type"] != "price_change"


def test_shared_category_link_is_not_a_product_identity():
    before = '<article><a href="/category">All products</a><h2>Product A</h2>14.99 EUR</article>'
    after = '<article><a href="/category">All products</a><h2>Product C</h2>2.99 EUR</article>'
    assert comparison(before, after)["change_type"] != "price_change"


def test_url_identity_survives_title_change():
    result = comparison(
        card("Old title", "14.99", path="/p/a"), card("Updated title", "12.99", path="/p/a")
    )
    assert result["change_type"] == "price_change"


def test_title_fallback():
    assert (
        comparison(card("Product A", "14.99"), card("Product A", "12.99"))["change_type"]
        == "price_change"
    )


def test_dom_context_fallback_without_heading():
    before = '<div class="product-card">BILLY bookshelf 49.99 EUR</div>'
    after = '<div class="product-card">BILLY bookshelf 39.99 EUR</div>'
    assert comparison(before, after)["change_type"] == "price_change"


def test_price_only_blocks_have_no_identity():
    before = '<div class="product-card">14.99 EUR</div>'
    after = '<div class="product-card">2.99 EUR</div>'
    assert comparison(before, after)["change_type"] != "price_change"


def test_multiple_prices_per_entity_suppressed():
    result = comparison(
        card("Product A", "14.99", "A", extra="<p>29.99 EUR</p>"), card("Product A", "2.99", "A")
    )
    assert result["change_type"] != "price_change"


def test_nested_feature_list_does_not_hide_product():
    entities = extract_entities(
        card("Product A", "14.99", "A", extra="<ul><li>Feature</li></ul>"), URL
    )
    assert len(entities) == 1 and entities[0]["entity_id"] == "A"


def test_explicit_unavailability():
    result = comparison(
        card("Product A", "14.99", "A"),
        card("Product A", "14.99", "A", extra="<p>Rupture de stock</p>"),
    )
    assert result["change_type"] == "unavailable"


def test_single_product_legacy_behavior():
    result = compare("Prix : 99 €", "Prix : 79 €")
    assert result["change_type"] == "price_change" and result["importance_score"] == 90


def test_single_unidentified_product_changed_title_after_price():
    result = compare("Price 14.99 EUR Product A", "Price 2.99 EUR Product C")
    assert result["change_type"] != "price_change"


def test_microdata_price_and_identity():
    def product(price):
        return f'<div itemtype="https://schema.org/Product"><h2 itemprop="name">Product A</h2><meta itemprop="sku" content="sku-A"><meta itemprop="price" content="{price}"><meta itemprop="priceCurrency" content="EUR"></div>'

    result = comparison(product("14.99"), product("12.99"))
    assert result["change_type"] == "price_change"
    assert result["entity_id"] == "sku-A"


def test_currency_change_is_not_price_change():
    before = card("Product A", "14.99", "A")
    after = card("Product A", "12.99", "A").replace("EUR", "USD")
    assert comparison(before, after)["change_type"] != "price_change"


def test_snapshot_migration_and_persistence():
    async def run():
        before, after = card("Product A", "14.99", "A"), card("Product A", "12.99", "A")
        text = extract_text(before)
        store = AsyncMock()
        store.get_value.return_value = {
            "schema_version": 1,
            "client_id": "demo",
            "url": URL,
            "text": text,
            "hash": text_hash(text),
        }
        output = AsyncMock()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text=after, headers={"content-type": "text/html"})
            )
        ) as client:
            await process_competitor("demo", {"name": "IKEA", "url": URL}, store, client, output)
        assert output.call_args.args[0]["change_type"] != "price_change"
        snapshot = store.set_value.call_args.args[1]
        assert snapshot["schema_version"] == 2 and snapshot["entities"][0]["entity_id"] == "A"
        store.get_value.return_value = snapshot
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text=before, headers={"content-type": "text/html"})
            )
        ) as client:
            await process_competitor("demo", {"name": "IKEA", "url": URL}, store, client, output)
        assert output.call_args.args[0]["entity_name"] == "Product A"
        assert output.call_args.args[0]["change_type"] == "price_change"
        assert "Product A" in output.call_args.args[0]["alert_html"]

    asyncio.run(run())


def test_unstructured_listing_fallback_is_suppressed():
    result = compare(
        "Deals 14.99 EUR Other 29.99 EUR",
        "Deals 2.99 EUR Other 29.99 EUR",
        previous_entities=[],
        current_entities=[],
        require_entity_match=True,
    )
    assert result["change_type"] != "price_change"
