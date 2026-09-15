"""Bounded HTML-only collection expansion using explicit same-origin links."""

import asyncio
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .entities import BLOCKS, identity
from .reliability import digest

MAX_PAGES = 10
MAX_ENTITIES = 500
MAX_BYTES = 25_000_000
MAX_SECONDS = 90
LABEL = re.compile(
    r"^(?:next(?: page)?|suivant(?:e)?|page suivante|afficher plus(?: de produits)?|(?:show|load) more(?: products)?)(?:\s*[›»→])?$",
    re.I,
)


def discover(html: str, base: str) -> tuple[list[str], bool]:
    soup = BeautifulSoup(html, "html.parser")
    origin = urlsplit(base)
    candidates = {}
    unsupported = False
    for node in soup.select("a, link, button, [data-next-url], [data-load-more-url]"):
        label = " ".join((node.get("aria-label") or node.get_text(" ", strip=True)).split())
        explicit = "next" in node.get("rel", []) or bool(LABEL.fullmatch(label))
        raw = (
            node.get("href")
            or node.get("data-next-url")
            or node.get("data-load-more-url")
            or (node.get("data-url") if explicit else None)
        )
        if explicit and not raw:
            unsupported = True
        if not isinstance(raw, str):
            continue
        try:
            parts = urlsplit(urljoin(base, raw))
            if (
                (parts.scheme, parts.hostname, parts.port)
                != (origin.scheme, origin.hostname, origin.port)
                or parts.username
                or parts.password
            ):
                if explicit:
                    unsupported = True
                continue
            params = parse_qs(parts.query)
            sequential = any(
                re.fullmatch(r"\d+", value)
                for key in ("page", "p", "pageNumber")
                for value in params.get(key, [])
            ) or bool(re.search(r"/page/\d+(?:/|$)", parts.path))
            if (
                explicit
                or sequential
                or node.has_attr("data-next-url")
                or node.has_attr("data-load-more-url")
            ):
                url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
                priority = 0 if explicit else 1 if sequential else 2
                candidates[url] = min(candidates.get(url, priority), priority)
        except ValueError:
            unsupported = True
    return sorted(candidates, key=candidates.get), unsupported


def repeats_next(html: str, base: str, visited: set[str]) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select('[rel~="next"][href]'):
        if urljoin(base, node["href"]).split("#")[0] in visited:
            return True
    return False


def canonical_entities(entities: list[dict]) -> list[dict]:
    unique = {}
    for entity in entities:
        key = identity(entity)
        if not key:
            continue
        if key in unique and unique[key] != entity:
            # Conflicting duplicate offers must not become a guessed price.
            unique[key] = {**unique[key], "price": None, "currency": None}
        else:
            unique[key] = dict(entity)
    return [unique[key] for key in sorted(unique)]


def collection_text(entities: list[dict], context: str = "") -> str:
    # Stable logical product content, independent of pagination/DOM order.
    return " ".join(
        [context, *(e.get("context", "") for e in canonical_entities(entities))]
    ).strip()


def collection_context(html: str) -> str:
    """Keep first-page commercial copy outside product blocks and pagination."""
    from .monitor import extract_text

    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select(BLOCKS):
        if node.parent:
            node.decompose()
    for node in soup.select("a, button, link"):
        label = " ".join((node.get("aria-label") or node.get_text(" ", strip=True)).split())
        if "next" in node.get("rel", []) or LABEL.fullmatch(label) or label.isdigit():
            node.decompose()
    try:
        return extract_text(str(soup))
    except ValueError:
        return ""


async def fetch_collection(
    client,
    url,
    fetcher,
    *,
    ignore_selectors=None,
    ignore_text_patterns=None,
    max_pages=MAX_PAGES,
    max_entities=MAX_ENTITIES,
    max_bytes=MAX_BYTES,
    max_seconds=MAX_SECONDS,
):
    started = time.monotonic()
    kwargs = {
        "include_entities": True,
        "ignore_selectors": ignore_selectors,
        "ignore_text_patterns": ignore_text_patterns,
    }
    async with asyncio.timeout(max_seconds):
        first = await fetcher(client, url, **kwargs)
    urls, unsupported = discover(first.get("_html", ""), first.get("_url", url))
    detected = len(first["entities"]) > 1 or bool(urls) or unsupported
    pages, total = 1, first.get("_bytes", 0)
    entities = canonical_entities(first["entities"])
    status = "unsupported" if unsupported else "complete"
    reason = (
        "No discoverable pagination or load-more URL"
        if unsupported
        else "1 page fetched; no next page found"
    )
    visited = {url, first.get("_url", url)}
    page_hashes = {digest(first["text"])}
    queue = [u for u in urls if u not in visited]
    if repeats_next(first.get("_html", ""), first.get("_url", url), visited):
        status, reason, queue = "partial", "Expansion stopped after repeated URL", []
    if detected and not entities:
        status, reason, queue = (
            "unsupported",
            "No identifiable products in HTML; expansion unavailable",
            [],
        )
    while queue:
        if (
            pages >= max_pages
            or len(entities) >= max_entities
            or total >= max_bytes
            or time.monotonic() - started >= max_seconds
        ):
            limit = (
                "max_collection_pages"
                if pages >= max_pages
                else "max_collection_entities"
                if len(entities) >= max_entities
                else "max_collection_total_bytes"
                if total >= max_bytes
                else "max_collection_duration_seconds"
            )
            value = {
                "max_collection_pages": max_pages,
                "max_collection_entities": max_entities,
                "max_collection_total_bytes": max_bytes,
                "max_collection_duration_seconds": max_seconds,
            }[limit]
            status, reason = "limited", f"Stopped at {limit}={value}"
            break
        target = queue.pop(0)
        if target in visited:
            continue
        visited.add(target)
        try:
            async with asyncio.timeout(max(0.001, max_seconds - (time.monotonic() - started))):
                page = await fetcher(
                    client, target, max_bytes=max_bytes - total, max_attempts=1, **kwargs
                )
            pages += 1
            total += page.get("_bytes", 0)
            page_hash = digest(page["text"])
            if page_hash in page_hashes:
                status, reason = "partial", "Expansion stopped after repeated page"
                break
            page_hashes.add(page_hash)
            combined = canonical_entities([*entities, *page["entities"]])
            if len(combined) == len(entities):
                status, reason = "partial", "Expansion stopped: no new unique entities"
                break
            entities = combined
            if repeats_next(page.get("_html", ""), page.get("_url", target), visited):
                status, reason = "partial", "Expansion stopped after repeated URL"
                break
            next_urls, blocked = discover(page.get("_html", ""), page.get("_url", target))
            if blocked:
                status, reason = "partial", "Additional page has unsupported load-more controls"
            queue.extend(u for u in next_urls if u not in visited and u not in queue)
            if not queue and not blocked and status == "complete":
                reason = f"{pages} pages fetched; no next page found"
        except Exception as exc:
            status, reason = (
                "partial",
                f"Additional page unavailable ({type(exc).__name__}); successful pages retained",
            )
            break
    if len(entities) > max_entities:
        entities = entities[:max_entities]
        status, reason = "limited", f"Stopped at max_collection_entities={max_entities}"
    if not detected:
        status, reason = "not_applicable", "Single-page content; no collection pagination detected"
    context = collection_context(first.get("_html", "")) if detected else ""
    return {
        **first,
        "entities": entities if detected else first["entities"],
        "text": collection_text(entities, context) if detected and entities else first["text"],
        "collection_context": context,
        "requires_entities": first["requires_entities"] or detected,
        "collection_detected": detected,
        "collection_pages_fetched": pages,
        "collection_entities_found": len(entities),
        "collection_expansion_status": status,
        "collection_expansion_reason": reason,
    }


def protect_coverage(page: dict, baseline: dict | None) -> tuple[dict, bool]:
    """Retain unobserved entities and the accepted baseline on incomplete scans."""
    if not baseline or not baseline.get("collection_detected"):
        return page, False
    incomplete = page["collection_expansion_status"] != "complete"
    lost_pages = page["collection_pages_fetched"] < baseline.get("collection_pages_fetched", 1)
    lost_entities = len(page["entities"]) < len(baseline.get("entities", []))
    if not incomplete and not (lost_pages and lost_entities):
        return page, False
    current = {identity(e) for e in page["entities"]}
    retained = [e for e in baseline.get("entities", []) if identity(e) not in current]
    merged = canonical_entities([*page["entities"], *retained])
    return {
        **page,
        "entities": merged,
        "text": collection_text(merged, page.get("collection_context", "")),
        "collection_removals_suppressed": len(retained),
        "collection_baseline_preserved": True,
        "collection_expansion_status": "partial"
        if not incomplete
        else page["collection_expansion_status"],
        "collection_expansion_reason": "Fewer pages and products than accepted baseline; removal coverage unconfirmed"
        if not incomplete
        else page["collection_expansion_reason"],
    }, True
