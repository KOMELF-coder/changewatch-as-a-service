"""Synthetic HTML mirrors observed semantic structure, not a vendor adapter."""

import asyncio
import re
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from src.collection import MAX_PAGES, fetch_collection, protect_coverage
from src.entities import extract_entities, identity
from src.monitor import compare, fetch_text

FIXTURES = Path(__file__).parent / "fixtures" / "semantic_listing"
BASE = "https://shop.example/fr/fr/cat/lowest-price/"


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def card(i, price="9,99", sku=False):
    attr = f'data-product-number="{i}"' if sku else ""
    return f'<div class="retail-tile" {attr}><div><a href="/fr/fr/p/product-{i}/#image"><img alt="Product {i}"></a></div><div><h3><a href="/fr/fr/p/product-{i}/#title">Product {i}</a></h3><span>Prix {price}€</span></div></div>'


def pages(total=351, prices=None, swap=False, added=False, removed=False):
    ids = list(range(1, total + 1))
    if swap:
        ids[0], ids[25] = ids[25], ids[0]
        ids[2], ids[3] = ids[3], ids[2]
    result = {}
    for start in range(0, total, 24):
        number = start // 24 + 1
        current = ids[start : start + 24]
        if removed and number == 12:
            current.remove(266)
        text = '<main><h1>Collection</h1><div class="product-list--compare-enabled">' + "".join(
            card(i, (prices or {}).get(i, "9,99")) for i in current
        )
        if added and number == 12:
            text += card(1000)
        text += "</div>"
        if start + 24 < total:
            text += f'<a href="?page={number + 1}#products-page-{number + 1}" aria-label="Afficher plus de produits">Afficher plus</a>'
        text += "</main>"
        result[BASE if number == 1 else BASE + f"?page={number}"] = text
    return result


def aggregate(mapping):
    async def run():
        calls = []

        def handler(request):
            calls.append(str(request.url))
            assert "#" not in str(request.url)
            return httpx.Response(
                200,
                text=mapping[str(request.url)],
                headers={"content-type": "text/html; charset=utf-8"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_collection(client, BASE, fetch_text)
        assert len(calls) == len(set(calls))
        return result

    return asyncio.run(run())


def comparison(before, after):
    return compare(
        before["text"],
        after["text"],
        previous_entities=before["entities"],
        current_entities=after["entities"],
        require_entity_match=True,
    )


def test_realistic_first_page_24_entities_without_ratings():
    entities = extract_entities(fixture("page-1"), BASE)
    assert len(entities) == 24
    assert entities[0]["entity_name"].startswith("BAGGEBO")
    assert entities[0]["price"] == "27.99" and entities[0]["currency"] == "EUR"
    assert entities[1]["entity_name"].startswith("PÄRKLA")
    assert entities[1]["price"] == "1.49"
    assert entities[0]["entity_id"] == "1"
    assert "Comparer" not in entities[0]["context"]


def test_three_realistic_fixture_pages():
    result = aggregate(
        {
            BASE: fixture("page-1"),
            BASE + "?page=2": fixture("page-2"),
            BASE + "?page=3": fixture("final-page"),
        }
    )
    assert result["collection_entities_found"] == 63
    assert result["collection_pages_fetched"] == 3
    assert result["collection_expansion_status"] == "complete"


def test_all_351_products_in_15_pages():
    result = aggregate(pages())
    assert MAX_PAGES == 20
    assert result["collection_detected"]
    assert result["collection_entities_found"] == 351
    assert result["collection_pages_fetched"] == 15
    assert result["collection_expansion_status"] == "complete"


def test_url_identity_without_sku_and_fragment_normalization():
    entity = extract_entities(card(1), BASE)[0]
    assert entity["entity_id"] is None
    assert identity(entity) == ("entity_url", "https://shop.example/fr/fr/p/product-1/")


def test_product_moves_and_reorders_without_any_change():
    assert not comparison(aggregate(pages(48)), aggregate(pages(48, swap=True)))["changed"]


@pytest.mark.parametrize(
    "kwargs,event,entity_id",
    [
        ({"prices": {170: "7,99"}}, "price_change", 170),
        ({"added": True}, "new_product", 1000),
        ({"removed": True}, "product_removed", 266),
    ],
)
def test_later_page_changes(kwargs, event, entity_id):
    result = comparison(aggregate(pages()), aggregate(pages(**kwargs)))
    assert len(result["entity_changes"]) == 1
    detected = result["entity_changes"][0]
    assert detected["change_type"] == event
    assert detected["entity_url"].endswith(f"/product-{entity_id}/")
    assert detected["change_confidence"] == 95


@pytest.mark.parametrize(
    "wrapper",
    [
        "nav",
        "footer",
        "form",
        'div class="filters"',
        'div class="cart"',
        'div class="checkout"',
        'div class="comparison"',
        'div role="dialog"',
        'div class="help"',
    ],
)
def test_noise_containers_do_not_become_products(wrapper):
    html = f"<{wrapper}>{card(1)}</{wrapper.split()[0]}>"
    assert extract_entities(html, BASE) == []


def test_controls_without_identity_and_cross_origin_links():
    html = (
        '<div><h3>Promotion</h3>99€</div><a href="?page=2">Afficher plus</a><button>Comparer 99€</button>'
        + card(1).replace("/fr/fr/p/", "https://other.example/p/")
    )
    assert extract_entities(html, BASE) == []


def test_multiple_prices_retains_url_but_never_guesses():
    html = card(1).replace("</span></div></div>", "</span><span>12,99€</span></div></div>")
    entity = extract_entities(html, BASE)[0]
    assert entity["entity_url"] and entity["price"] is None and entity["currency"] is None


@pytest.mark.parametrize("path", ["/product/", "/products/", "/produit/", "/produits/"])
def test_generic_detail_link_patterns(path):
    assert len(extract_entities(card(1).replace("/fr/fr/p/", path), BASE)) == 1


def test_fixture_is_synthetic_and_has_required_pagination():
    soup = BeautifulSoup(fixture("page-1"), "html.parser")
    assert (
        soup.find("a", attrs={"aria-label": "Afficher plus de produits"})["href"]
        == "?page=2#products-page-2"
    )
    assert re.search("Afficher 24 résultats sur 351", soup.get_text())


@pytest.mark.parametrize("title", ["Comparer", "Reviews (128)", "99€"])
def test_control_title_is_not_a_product(title):
    assert (
        extract_entities(f'<div><a href="/p/test/">{title}</a><span>9,99€</span></div>', BASE) == []
    )


def test_semantic_partial_scan_suppresses_missing_later_products():
    before = aggregate(pages())
    current = aggregate(pages(24))
    current["collection_expansion_status"] = "partial"
    protected, preserve = protect_coverage(current, before)
    assert preserve
    assert protected["collection_removals_suppressed"] == 327
    assert not comparison(before, protected)["entity_changes"]
