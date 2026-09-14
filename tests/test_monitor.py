import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src.main import process_competitor
from src.monitor import (
    canonical_url,
    compare,
    extract_text,
    fetch_text,
    snapshot_key,
    validate_input,
)


def test_extraction():
    a = "<head><title>Noise</title></head><nav>Menu</nav><p>Monthly <b>plan</b> $10</p><footer>2025</footer>"
    b = '<p>Monthly plan\n $10</p><script>noise</script><div hidden>hidden</div><span style="display:none">hidden</span>'
    assert extract_text(a) == extract_text(b) == "Monthly plan $10"
    assert compare(extract_text(a), extract_text(b))["status"] == "unchanged"


def test_nested_noise():
    assert extract_text("<nav><span hidden>menu</span></nav><p>Hello</p>") == "Hello"


def test_nested_inline_styles():
    assert (
        extract_text(
            '<div style="display:none"><span style="color:red">Hidden</span></div><p>Hello</p>'
        )
        == "Hello"
    )


def test_initialization():
    result = compare(None, "Hello world")
    assert result["status"] == "initialized"
    assert result["previous_hash"] is None
    assert result["changed"] is False
    assert result["importance_score"] == 0
    assert compare("Hello\n world", "Hello world")["changed"] is False


def test_scoring():
    price = compare("Monthly plan price $10", "Monthly plan price $20")
    minor = compare("Our team likes blue", "Our team likes green")
    assert price["importance_score"] > minor["importance_score"]
    assert "pricing" in price["matched_concepts"]
    assert price["changes"] == [{"removed": "$10", "added": "$20"}]
    assert price["previous_hash"] != price["current_hash"]
    assert price == compare("Monthly plan price $10", "Monthly plan price $20")
    assert 0 <= price["importance_score"] <= 100


def test_unrelated_keywords():
    prefix = (
        "Pricing discount launch subscription one two three four five six seven eight nine ten "
    )
    assert compare(prefix + "blue", prefix + "green")["matched_concepts"] == []


def test_deletions_and_limits():
    assert "promotion" in compare("Sale ends today", "Welcome")["matched_concepts"]
    result = compare("x" * 1000, "y" * 1000)
    assert result["diff_truncated"]
    assert len(result["changes"][0]["added"]) == 500


def test_identity():
    assert canonical_url("https://EXAMPLE.com:443#part") == "https://example.com/"
    assert snapshot_key("a", "https://example.com/") != snapshot_key("b", "https://example.com/")
    assert snapshot_key("a", "https://example.com/") != snapshot_key(
        "a", "https://example.com/pricing"
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"client_id": " ", "competitors": []},
        {"client_id": "a", "competitors": [{"name": "A", "url": "file:///tmp/test"}]},
        {
            "client_id": "a",
            "competitors": [{"name": "A", "url": "https://u:p@example.com"}],
        },
        {
            "client_id": "a",
            "competitors": [{"name": "A", "url": "https://example.com:bad"}],
        },
        {
            "client_id": "a",
            "competitors": [
                {"name": "A", "url": "https://example.com"},
                {"name": "B", "url": "https://example.com/"},
            ],
        },
    ],
)
def test_invalid_input(data):
    with pytest.raises(ValueError):
        validate_input(data)


class Store:
    def __init__(self):
        self.records = {}

    async def get_value(self, key):
        return self.records.get(key)

    async def set_value(self, key, value):
        self.records[key] = value


def test_lifecycle():
    async def run():
        store, output = Store(), []
        page, status = "<p>Price $10</p>", 200

        async def push(result):
            output.append(result)

        def handler(request):
            return httpx.Response(status, text=page, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            entry = {"name": "A", "url": "https://example.com/"}
            for content in [
                "<p>Price $10</p>",
                "<div>Price  $10</div>",
                "<p>Price $20</p>",
            ]:
                page = content
                await process_competitor("a", entry, store, client, push)
            baseline = dict(store.records)
            status = 404
            assert not await process_competitor("a", entry, store, client, push)
            assert store.records == baseline
            status = 200
            await process_competitor("a", entry, store, client, push)
        assert [r["status"] for r in output] == [
            "initialized",
            "unchanged",
            "changed",
            "error",
            "changed",
        ]

    asyncio.run(run())


def test_dataset_failure():
    async def run():
        store = Store()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text="<p>Hello</p>", headers={"content-type": "text/html"}
                )
            )
        ) as client:
            with pytest.raises(RuntimeError):
                await process_competitor(
                    "a",
                    {"name": "A", "url": "https://example.com/"},
                    store,
                    client,
                    AsyncMock(side_effect=RuntimeError("storage unavailable")),
                )
        assert not any(key.startswith("snapshot-") for key in store.records)

    asyncio.run(run())


@pytest.mark.parametrize(
    "content,mime",
    [
        ("", "text/html"),
        ("{}", "application/json"),
        ("<p>" + "a" * 200001 + "</p>", "text/html"),
    ],
    ids=["empty", "non-html", "oversized"],
)
def test_unusable_pages(content, mime):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, text=content, headers={"content-type": mime})
            )
        ) as client:
            with pytest.raises(ValueError):
                await fetch_text(client, "https://example.com/")

    asyncio.run(run())


def test_retry(monkeypatch):
    monkeypatch.setattr("src.monitor.asyncio.sleep", AsyncMock())

    async def run():
        statuses = iter([503, 429, 200])
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    next(statuses),
                    text="<p>Ready</p>",
                    headers={"content-type": "text/html"},
                )
            )
        ) as client:
            assert await fetch_text(client, "https://example.com/") == "Ready"

    asyncio.run(run())
