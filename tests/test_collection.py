import asyncio
import copy
from unittest.mock import AsyncMock

import httpx
import pytest

from src.collection import discover, fetch_collection
from src.monitor import compare, fetch_text, snapshot_key
from src.processing import process_competitor


class Store:
    def __init__(self):
        self.records = {}

    async def get_value(self, key):
        return copy.deepcopy(self.records.get(key))

    async def set_value(self, key, value):
        self.records[key] = copy.deepcopy(value)


def card(i, price=99):
    return f'<article data-sku="{i}"><h2>Product {i}</h2><span>{price} EUR</span></article>'


def page(ids, link=None, prices=None):
    return "".join(card(i, (prices or {}).get(i, 99)) for i in ids) + (
        f'<a rel="next" href="{link}">Next</a>' if link else ""
    )


def client_for(pages):
    def handler(request):
        value = pages.get(str(request.url), 404)
        return httpx.Response(
            value if isinstance(value, int) else 200,
            text=value if isinstance(value, str) else "",
            headers={"content-type": "text/html"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


BASE = "https://example.com/category"


def expand(pages, **limits):
    async def run():
        async with client_for(pages) as client:
            return await fetch_collection(client, BASE, fetch_text, **limits)

    return asyncio.run(run())


def fixture():
    return {
        BASE: page(range(24), "?page=2"),
        BASE + "?page=2": page(range(24, 48), "?page=3"),
        BASE + "?page=3": page(range(48, 60)),
    }


def test_ikea_like_three_pages():
    result = expand(fixture())
    assert result["collection_detected"]
    assert result["collection_pages_fetched"] == 3
    assert result["collection_entities_found"] == 60
    assert result["collection_expansion_status"] == "complete"


@pytest.mark.parametrize(
    "markup",
    [
        '<link rel="next" href="?page=2">',
        '<a href="?page=2">2</a>',
        '<a href="/page/2">2</a>',
        '<button data-next-url="?page=2">Load more</button>',
        '<a href="?page=2">Page suivante</a>',
    ],
)
def test_discovery(markup):
    urls, _ = discover(markup, BASE)
    assert len(urls) == 1


def test_no_invented_or_cross_origin_urls():
    urls, unsupported = discover(
        '<a rel="next" href="https://other.example/page/2">Next</a><button>Load more</button>', BASE
    )
    assert not urls and unsupported


def test_repeated_page_stops():
    first = page([1, 2], "?page=2")
    result = expand({BASE: first, BASE + "?page=2": first})
    assert result["collection_pages_fetched"] == 2
    assert result["collection_expansion_status"] == "partial"


def test_no_next_page():
    result = expand({BASE: page([1, 2])})
    assert result["collection_pages_fetched"] == 1
    assert result["collection_expansion_status"] == "complete"


def test_js_only_load_more():
    result = expand({BASE: page([1, 2]) + "<button>Load more</button>"})
    assert result["collection_expansion_status"] == "unsupported"


@pytest.mark.parametrize(
    "limit,value", [("max_pages", 2), ("max_entities", 25), ("max_bytes", 100)]
)
def test_limits(limit, value):
    result = expand(fixture(), **{limit: value})
    assert result["collection_expansion_status"] == "limited"
    if limit == "max_entities":
        assert len(result["entities"]) == value


def test_dedup_and_page_position_does_not_change():
    before = expand({BASE: page([1, 2], "?page=2"), BASE + "?page=2": page([2, 3])})
    after = expand({BASE: page([3, 2], "?page=2"), BASE + "?page=2": page([1, 2])})
    assert len(before["entities"]) == 3
    result = compare(
        before["text"],
        after["text"],
        previous_entities=before["entities"],
        current_entities=after["entities"],
        require_entity_match=True,
    )
    assert not result["changed"]


@pytest.mark.parametrize(
    "ids,prices,event",
    [([3, 4], {}, "new_product"), ([3], {}, "product_removed"), ([3, 4], {4: 79}, "price_change")],
)
def test_later_page_changes(ids, prices, event):
    before_ids = [3] if event == "new_product" else [3, 4]
    before = expand({BASE: page([1, 2], "?page=2"), BASE + "?page=2": page(before_ids)})
    after = expand({BASE: page([1, 2], "?page=2"), BASE + "?page=2": page(ids, prices=prices)})
    result = compare(
        before["text"],
        after["text"],
        previous_entities=before["entities"],
        current_entities=after["entities"],
        require_entity_match=True,
    )
    assert len(result["entity_changes"]) == 1
    assert result["entity_changes"][0]["change_type"] == event


def test_partial_preserves_good_snapshot_and_no_mass_removals():
    async def run():
        store, output = Store(), []
        sender = AsyncMock(return_value=(True, None))

        async def push(item):
            output.append(item)

        async with client_for(fixture()) as client:
            await process_competitor(
                "demo", {"name": "Shop", "url": BASE}, store, client, push, sender=sender
            )
        key = snapshot_key("demo", BASE)
        saved = copy.deepcopy(store.records[key])
        broken = fixture()
        broken[BASE + "?page=2"] = 403
        async with client_for(broken) as client:
            await process_competitor(
                "demo", {"name": "Shop", "url": BASE}, store, client, push, sender=sender
            )
        assert output[-1]["collection_expansion_status"] == "partial"
        assert output[-1]["collection_entities_found"] == 24
        assert output[-1]["collection_removals_suppressed"] == 36
        assert not output[-1]["entity_changes"]
        assert store.records[key] == saved
        assert not output[-1]["alert_sent"]

    asyncio.run(run())


def test_initial_fetch_failure_preserves_snapshot():
    async def run():
        store = Store()
        async with client_for(fixture()) as client:
            await process_competitor(
                "demo", {"name": "Shop", "url": BASE}, store, client, AsyncMock()
            )
        saved = copy.deepcopy(store.records[snapshot_key("demo", BASE)])
        async with client_for({BASE: 403}) as client:
            assert not await process_competitor(
                "demo", {"name": "Shop", "url": BASE}, store, client, AsyncMock()
            )
        assert store.records[snapshot_key("demo", BASE)] == saved

    asyncio.run(run())


def test_repeated_next_url():
    result = expand({BASE: page([1, 2], "?page=2"), BASE + "?page=2": page([3, 4], BASE)})
    assert result["collection_expansion_status"] == "partial"
    assert "repeated URL" in result["collection_expansion_reason"]


def test_nonproduct_promotional_copy_remains_monitored():
    before = expand({BASE: "<p>Promotion summer</p>" + page([1, 2])})
    after = expand({BASE: "<p>Promotion autumn</p>" + page([1, 2])})
    assert "Promotion summer" in before["text"]
    assert before["text"] != after["text"]


def test_expansion_time_budget_is_nonfatal():
    async def run():
        async def fetcher(client, url, **kwargs):
            if url != BASE:
                await asyncio.sleep(0.1)
            return {
                "text": "Product",
                "entities": [{"entity_id": "a", "context": "Product"}],
                "requires_entities": True,
                "_html": '<a rel="next" href="?page=2">Next</a>',
            }

        result = await fetch_collection(None, BASE, fetcher, max_seconds=0.02)
        assert result["collection_expansion_status"] in {"partial", "limited"}
        assert result["collection_entities_found"] == 1

    asyncio.run(run())


def test_migration_and_lost_pagination_are_safe():
    async def run():
        store, output = Store(), []

        async def push(item):
            output.append(item)

        async with client_for(fixture()) as client:
            await process_competitor("demo", {"name": "Shop", "url": BASE}, store, client, push)
        key = snapshot_key("demo", BASE)
        # An existing schema-2 snapshot without collection metadata upgrades once.
        store.records[key] = {
            k: v for k, v in store.records[key].items() if not k.startswith("collection_")
        }
        store.records.pop(key.replace("snapshot-", "monitor-"))
        async with client_for(fixture()) as client:
            await process_competitor("demo", {"name": "Shop", "url": BASE}, store, client, push)
        assert output[-1]["collection_migrated"] and output[-1]["status"] == "initialized"
        saved = copy.deepcopy(store.records[key])
        async with client_for({BASE: page(range(24))}) as client:
            await process_competitor("demo", {"name": "Shop", "url": BASE}, store, client, push)
        assert output[-1]["collection_expansion_status"] == "partial"
        assert not output[-1]["entity_changes"]
        assert store.records[key] == saved

    asyncio.run(run())


def test_partial_price_change_deduplicates_until_full_recovery():
    async def run():
        store, output, sender = Store(), [], AsyncMock(return_value=(True, None))

        async def push(item):
            output.append(item)

        original = fixture()
        changed = fixture()
        changed[BASE] = page(range(24), "?page=2", prices={1: 79})
        broken = {**changed, BASE + "?page=2": 403}
        for pages in (original, broken, broken, changed):
            async with client_for(pages) as client:
                await process_competitor(
                    "demo",
                    {"name": "Shop", "url": BASE},
                    store,
                    client,
                    push,
                    {"client_email": "a@example.com"},
                    sender,
                )
        assert sender.await_count == 1
        assert output[1]["entity_changes"][0]["change_type"] == "price_change"
        assert output[2]["duplicate_alert_suppressed"]
        assert store.records[snapshot_key("demo", BASE)]["collection_entities_found"] == 60

    asyncio.run(run())
