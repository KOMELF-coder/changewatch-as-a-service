"""Small, deterministic fetching, extraction and comparison helpers."""

import asyncio
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .alerts import validate_alert_options
from .entities import compare_entities, extract_entities
from .prices import MONEY, detect_price_changes, price_score_floor, tokens

MAX_BYTES = 5_000_000
MAX_TEXT = 200_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CONCEPTS = {
    "pricing": (30, r"\b(?:prices?|pricing|costs?|tarifs?|prix|coûts?)\b"),
    "promotion": (25, r"\b(?:discounts?|promotions?|promos?|sales?|réductions?|remises?|soldes)\b"),
    "new offering": (25, r"\b(?:products?|services?|produits?|nouveau produit|nouveau service)\b"),
    "shipping": (15, r"\b(?:shipping|delivery|livraisons?|frais de port)\b"),
    "subscription": (
        25,
        r"\b(?:subscriptions?|monthly|annual|free trials?|abonnements?|mensuel(?:le)?s?|annuel(?:le)?s?)\b",
    ),
    "feature": (15, r"\b(?:features?|fonctionnalités?)\b"),
    "launch": (25, r"\b(?:launch(?:es|ed|ing)?|lancements?|nouveautés?)\b"),
    "availability": (
        20,
        r"\b(?:availability|available|unavailable|stocks?|disponibilité|disponible|indisponible|rupture)\b",
    ),
}


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL")
    if parts.username or parts.password or any(c.isspace() for c in url.strip()):
        raise ValueError("URL must not contain credentials or whitespace")
    port = parts.port  # Validate malformed ports before making a request.
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    if port and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))


def validate_input(data: object) -> tuple[str, list[dict]]:
    required = {"client_id", "competitors"}
    if (
        not isinstance(data, dict)
        or not required <= set(data)
        or set(data) - required - {"client_email", "alert_threshold", "language"}
    ):
        raise ValueError(
            "Input requires client_id and competitors, with optional client_email, alert_threshold and language"
        )
    validate_alert_options(data)
    client = data["client_id"]
    if not isinstance(client, str) or not 1 <= len(client.strip()) <= 200:
        raise ValueError("client_id must be a nonempty string of up to 200 characters")
    entries = data["competitors"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("competitors must contain 1 to 100 entries")
    competitors, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name", "url"}:
            raise ValueError("Each competitor must contain name and url only")
        name, url = entry["name"], entry["url"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
            raise ValueError("Competitor name must contain 1 to 200 characters")
        if not isinstance(url, str) or len(url) > 2048:
            raise ValueError("Competitor URL must be a string of up to 2048 characters")
        url = canonical_url(url)
        if url in seen:
            raise ValueError("Duplicate competitor URL; use one entry per page")
        seen.add(url)
        competitors.append({"name": name.strip(), "url": url})
    return client.strip(), competitors


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_key(client_id: str, url: str) -> str:
    return "snapshot-" + text_hash(json.dumps([client_id, url], ensure_ascii=False))


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select(
        "script, style, noscript, template, nav, footer, head, "
        '[hidden], [aria-hidden="true"], [role="navigation"], [role="contentinfo"]'
    ):
        tag.decompose()
    for tag in soup.select("[style]"):
        if tag.attrs is None:  # An enclosing hidden element was already decomposed.
            continue
        if re.search(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
            tag.get("style", ""),
            re.I,
        ):
            tag.decompose()
    # Separate blocks while preserving inline words such as <strong>sale</strong>.
    for tag in soup.find_all(
        [
            "p",
            "div",
            "section",
            "article",
            "li",
            "br",
            "tr",
            "td",
            "th",
            "h1",
            "h2",
            "h3",
            "h4",
        ]
    ):
        tag.insert_before(" ")
        tag.insert_after(" ")
    text = normalize(soup.get_text())
    if not text:
        raise ValueError("Page contains no usable visible text")
    if len(text) > MAX_TEXT:
        raise ValueError("Extracted text exceeds 200,000 characters")
    return text


async def fetch_text(
    client: httpx.AsyncClient, url: str, *, include_entities: bool = False
) -> str | dict:
    for attempt in range(3):
        try:
            async with asyncio.timeout(60):
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if mime not in {"text/html", "application/xhtml+xml"}:
                        raise ValueError("Expected an HTML page")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError("Page exceeds 5 MB decompressed limit")
                    html = bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    text = extract_text(html)
                    if include_entities:
                        return {
                            "text": text,
                            "entities": extract_entities(html, str(response.url)),
                            "requires_entities": sum(isinstance(t, tuple) for t in tokens(text)) > 1
                            or bool(
                                re.search(
                                    r"data-(?:sku|product-id)|product-card|product-item|product-block|plp-product-list|schema.org/Product",
                                    html,
                                    re.I,
                                )
                            ),
                        }
                    return text
        except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
            retryable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in {
                408,
                429,
                500,
                502,
                503,
                504,
            }
            if attempt == 2 or not retryable:
                raise
            await asyncio.sleep(2**attempt)
    raise RuntimeError("Fetch attempts exhausted")


def fragment_concepts(words: list[str], start: int, end: int) -> set[str]:
    """Match edited words plus an immediately preceding commercial label.

    A label must end at the edit (apart from a colon), so unchanged neighboring
    sections and trailing page content cannot donate unrelated concepts.
    """
    if start == end:
        return set()
    fragment = " ".join(words[start:end]).lower()
    found = {name for name, (_, pattern) in CONCEPTS.items() if re.search(pattern, fragment)}
    prefix = " ".join(words[max(0, start - 4) : start]).lower()
    labels = []
    for name, (_, pattern) in CONCEPTS.items():
        for match in re.finditer(pattern, prefix):
            if re.fullmatch(r"\s*:?\s*", prefix[match.end() :]):
                labels.append((match.start(), name))
    if labels:
        # Prefer the nearest label, rather than a bag of all nearby keywords.
        found.add(max(labels)[1])
    return found


def compare(
    previous: str | None,
    current: str,
    *,
    previous_entities: list | None = None,
    current_entities: list | None = None,
    require_entity_match: bool = False,
) -> dict:
    current = normalize(current)
    previous = normalize(previous) if previous is not None else None
    base = {
        "previous_hash": text_hash(previous) if previous is not None else None,
        "current_hash": text_hash(current),
        "changed": False,
        "importance_score": 0,
        "matched_concepts": [],
        "changes": [],
        "diff_truncated": False,
        "entity_changes": [],
    }
    if previous is None:
        return {
            **base,
            "status": "initialized",
            "similarity_ratio": None,
            "change_summary": "Initial snapshot saved; no alert.",
        }
    entity_mode = require_entity_match or bool(previous_entities or current_entities)
    entity_changes = (
        compare_entities(previous_entities, current_entities)
        if entity_mode and previous_entities is not None and current_entities is not None
        else []
    )
    if previous == current and not entity_changes:
        return {
            **base,
            "status": "unchanged",
            "similarity_ratio": 1.0,
            "change_summary": "No textual content changes.",
        }
    old, new = previous.split(), current.split()
    matcher = SequenceMatcher(None, old, new, autojunk=True)
    changes, contexts = [], []
    concepts = set()
    for kind, i, j, a, b in matcher.get_opcodes():
        if kind == "equal":
            continue
        changes.append({"removed": " ".join(old[i:j]), "added": " ".join(new[a:b])})
        concepts.update(fragment_concepts(old, i, j))
        concepts.update(fragment_concepts(new, a, b))
        # Retain the existing monetary-bonus window, separately from concept matching.
        contexts.extend(
            [" ".join(old[max(0, i - 6) : j + 6]), " ".join(new[max(0, a - 6) : b + 6])]
        )
    price_changes = (
        [event for event in entity_changes if event["change_type"] == "price_change"]
        if entity_mode
        else detect_price_changes(previous, current)
    )
    if any(event["change_type"] in {"new_product", "product_removed"} for event in entity_changes):
        concepts.add("new offering")
    if any(event["change_type"] == "unavailable" for event in entity_changes):
        concepts.add("availability")
    if price_changes:
        concepts.add("pricing")
    matched = [name for name in CONCEPTS if name in concepts]
    ratio = matcher.ratio()
    money = any(MONEY.search(c) or re.search(r"\d[\d.,]*\s*%", c) for c in contexts)
    score = min(
        100,
        10 + round(20 * (1 - ratio)) + sum(CONCEPTS[k][0] for k in matched) + (20 if money else 0),
    )
    price_fields = {"change_type": "generic_content_change"}
    if entity_changes:
        price_fields.update(entity_changes[0])
    if price_changes:
        # The top-level fields describe the most material detected price edit.
        primary = max(price_changes, key=price_score_floor)
        score = max(score, price_score_floor(primary))
        price_fields = {"change_type": "price_change", **primary, "price_changes": price_changes}
    bounded = [{k: v[:500] for k, v in change.items()} for change in changes[:10]]
    excerpt = bounded[0] if bounded else {"removed": "", "added": ""}
    summary = f"{len(changes)} text change(s)"
    if matched:
        summary += f"; concepts: {', '.join(matched)}"
    summary += f". Before: {excerpt['removed'][:140] or '(none)'}. After: {excerpt['added'][:140] or '(none)'}."
    if entity_changes:
        summary += (
            " Entities: "
            + "; ".join(
                f"{e['change_type']}: {e.get('entity_name') or e.get('entity_id') or e.get('entity_url')}"
                for e in entity_changes[:10]
            )
            + "."
        )
    return {
        **base,
        **price_fields,
        "status": "changed",
        "changed": True,
        "importance_score": score,
        "similarity_ratio": round(ratio, 6),
        "matched_concepts": matched,
        "changes": bounded,
        "diff_truncated": bounded != changes,
        "change_summary": summary,
        "entity_changes": entity_changes,
    }
