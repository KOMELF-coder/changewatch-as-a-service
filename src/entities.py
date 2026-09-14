"""Conservative DOM product extraction and stable-identity matching."""

import re
from collections import Counter
from decimal import Decimal
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .prices import MONEY, price_difference, tokens

BLOCKS = '[itemtype$="/Product"], [data-product-id], [data-sku], .product-card, .product-item, .product, .product-small, .product-block, .plp-product-list__item, article, li'


def clean(text: str) -> str:
    return " ".join(text.split())


def product_url(value: str, base: str) -> str | None:
    url = urlsplit(urljoin(base, value))
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        return None
    return urlunsplit((url.scheme, url.netloc.lower(), url.path, url.query, ""))


def extract_entities(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select('script, style, nav, footer, [hidden], [aria-hidden="true"]'):
        node.decompose()
    candidates = soup.select(BLOCKS)
    entities = []
    for block in candidates:
        heading = block.select_one(
            '[itemprop="name"], .product-title, .product-name, h1, h2, h3, h4'
        )
        link = block.select_one(
            'a[itemprop="url"], a[href*="/product/"], a[href*="/products/"], a[href*="/p/"]'
        )
        if not link and heading:
            link = heading.select_one("a[href]")
            if not link:
                parent_link = heading.find_parent("a", href=True)
                if parent_link and block in parent_link.parents:
                    link = parent_link
        title = clean(heading.get_text(" ", strip=True)) if heading else None
        if not title and link:
            title = clean(link.get_text(" ", strip=True)) or None
        sku = block.get("data-sku") or block.get("data-product-id")
        sku_node = block.select_one('[itemprop="sku"], [itemprop="productID"]')
        if not sku and sku_node:
            sku = sku_node.get("content") or sku_node.get_text(strip=True)
        text = clean(block.get_text(" ", strip=True))
        prices = [token for token in tokens(text) if isinstance(token, tuple)]
        # Explicit microdata price is useful when the currency is not visible.
        price_node = block.select_one('[itemprop="price"]')
        currency_node = block.select_one('[itemprop="priceCurrency"]')
        if (
            not prices
            and price_node
            and currency_node
            and len(block.select('[itemprop="price"]')) == 1
        ):
            raw = price_node.get("content") or price_node.get_text(strip=True)
            currency = currency_node.get("content") or currency_node.get_text(strip=True)
            if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", raw) and currency in {"EUR", "USD", "GBP"}:
                prices = [(currency, Decimal(raw.replace(",", ".")))]
        url = product_url(link["href"], base_url) if link and link.get("href") else None
        context_identity = clean(MONEY.sub(" ", text)).strip(" -:;|").casefold()
        # Last resort: exact surrounding block text, never DOM position or price.
        if (
            len(context_identity) > 2000
            or not re.search(r"[a-zà-ÿ]{3}", context_identity)
            or re.fullmatch(
                r"(?:price|prix|pricing|sale|promo|promotion|cost|tarif|€|\W)+", context_identity
            )
        ):
            context_identity = None
        if not (sku or title or url or context_identity) or (
            not prices and not sku and not block.select_one('[itemprop="availability"]')
        ):
            continue
        availability_node = block.select_one('[itemprop="availability"]')
        availability_text = (
            text + " " + (availability_node.get("href", "") if availability_node else "")
        )
        unavailable = bool(
            re.search(
                r"outofstock|out of stock|rupture|indisponible|unavailable", availability_text, re.I
            )
        )
        # Multiple prices (variants, crossed-out offers) are ambiguous, not paired.
        entities.append(
            (
                block,
                {
                    "entity_id": str(sku) if sku else None,
                    "entity_name": title,
                    "entity_url": url,
                    "price": str(prices[0][1]) if len(prices) == 1 else None,
                    "currency": prices[0][0] if len(prices) == 1 else None,
                    "availability": "unavailable" if unavailable else None,
                    "context": text[:2000],
                    "entity_context": context_identity,
                },
            )
        )
    # Keep the smallest independently identifiable blocks, not listing wrappers.
    ancestors = {id(parent) for block, _ in entities for parent in block.parents}
    return [entity for block, entity in entities if id(block) not in ancestors]


def identity(entity: dict) -> tuple | None:
    for field in ("entity_id", "entity_url", "entity_name", "entity_context"):
        if entity.get(field):
            value = clean(entity[field])
            return field, value.casefold() if field == "entity_name" else value
    return None


def compare_entities(previous: list[dict], current: list[dict]) -> list[dict]:
    old_counts = Counter(identity(e) for e in previous)
    new_counts = Counter(identity(e) for e in current)
    old = {identity(e): e for e in previous if identity(e) and old_counts[identity(e)] == 1}
    new = {identity(e): e for e in current if identity(e) and new_counts[identity(e)] == 1}
    events = []
    for key in dict.fromkeys([*old, *new]):
        if old_counts[key] > 1 or new_counts[key] > 1:
            continue
        before, after = old.get(key), new.get(key)
        entity = after or before
        fields = {k: entity.get(k) for k in ("entity_name", "entity_url", "entity_id")}
        fields["change_confidence"] = {
            "entity_id": 100,
            "entity_url": 95,
            "entity_name": 80,
            "entity_context": 70,
        }[key[0]]
        if before is None:
            events.append({"change_type": "new_product", **fields})
        elif after is None:
            events.append({"change_type": "product_removed", **fields})
        else:
            if before.get("price") is not None and after.get("price") is not None:
                difference = price_difference(
                    (before["currency"], Decimal(before["price"])),
                    (after["currency"], Decimal(after["price"])),
                )
                if difference and fields["change_confidence"] >= 80:
                    events.append({"change_type": "price_change", **fields, **difference})
            if (
                after.get("availability") == "unavailable"
                and before.get("availability") != "unavailable"
            ):
                events.append({"change_type": "unavailable", **fields})
    return events
