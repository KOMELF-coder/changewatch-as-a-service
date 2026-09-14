"""Customer namespace safety and the documented production input contract."""

import json
from pathlib import Path

import pytest

from src.monitor import snapshot_key, validate_input

ROOT = Path(__file__).resolve().parents[1]


def config(client_id):
    return {
        "client_id": client_id,
        "competitors": [{"name": "Shop", "url": "https://example.com/"}],
    }


@pytest.mark.parametrize(
    "client_id", ["client-acme", "maison-dupont", "agency-client-001", "a", "a" * 200]
)
def test_canonical_id_preserves_existing_namespace(client_id):
    normalized, _ = validate_input(config(client_id))
    assert normalized == client_id
    assert snapshot_key(normalized, "https://example.com/") == snapshot_key(
        client_id, "https://example.com/"
    )


@pytest.mark.parametrize(
    "client_id",
    [
        None,
        42,
        "",
        " ",
        "Client-Acme",
        " client-acme",
        "client-acme ",
        "client-acme\n",
        "client_acme",
        "client--acme",
        "-client",
        "client-",
        "../client",
        "client/acme",
        "maison-école",
        "ａｃｍｅ",
        "client\x00acme",
        "a" * 201,
    ],
)
def test_invalid_ids_never_silently_map_to_another_namespace(client_id):
    with pytest.raises(ValueError, match="client_id"):
        validate_input(config(client_id))


def test_distinct_client_ids_remain_isolated():
    first, _ = validate_input(config("client-acme"))
    second, _ = validate_input(config("client-acme-001"))
    assert snapshot_key(first, "https://example.com/") != snapshot_key(
        second, "https://example.com/"
    )


@pytest.mark.parametrize("plan,count", [("starter", 5), ("business", 15)])
def test_production_examples_match_contract(plan, count):
    data = json.loads((ROOT / "examples" / f"client-{plan}.json").read_text(encoding="utf-8"))
    client, competitors = validate_input(data)
    assert client == f"client-example-{plan}"
    assert len(competitors) == count
    assert len({entry["url"] for entry in competitors}) == count
    assert set(data) == {
        "client_id",
        "client_email",
        "language",
        "timezone",
        "alert_threshold",
        "competitors",
    }
    assert data["alert_threshold"] == 60
    assert all(".example.com/" in entry["url"] for entry in competitors)
