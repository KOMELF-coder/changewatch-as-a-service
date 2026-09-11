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

MAX_BYTES = 5_000_000
MAX_TEXT = 200_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CONCEPTS = {
    "pricing": (30, r"\bpric(?:e|es|ing)\b"),
    "promotion": (25, r"\b(?:discounts?|promotions?|sale|sales)\b"),
    "new offering": (25, r"\bnew (?:products?|services?)\b"),
    "shipping": (15, r"\b(?:shipping|delivery)\b"),
    "subscription": (
        25,
        r"\b(?:subscriptions?|annual plans?|monthly plans?|free trials?)\b",
    ),
    "feature": (15, r"\bfeatures?\b"),
    "launch": (25, r"\blaunch(?:es|ed|ing)?\b"),
    "availability": (20, r"\b(?:availability|available|unavailable|stock)\b"),
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
    if not isinstance(data, dict) or set(data) != {"client_id", "competitors"}:
        raise ValueError("Input must contain client_id and competitors only")
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


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
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
                    return extract_text(
                        bytes(body).decode(response.encoding or "utf-8", errors="replace")
                    )
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


def compare(previous: str | None, current: str) -> dict:
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
    }
    if previous is None:
        return {
            **base,
            "status": "initialized",
            "similarity_ratio": None,
            "change_summary": "Initial snapshot saved; no alert.",
        }
    if previous == current:
        return {
            **base,
            "status": "unchanged",
            "similarity_ratio": 1.0,
            "change_summary": "No textual content changes.",
        }
    old, new = previous.split(), current.split()
    matcher = SequenceMatcher(None, old, new, autojunk=True)
    changes, contexts = [], []
    for kind, i, j, a, b in matcher.get_opcodes():
        if kind == "equal":
            continue
        changes.append({"removed": " ".join(old[i:j]), "added": " ".join(new[a:b])})
        # Nearby words let a price-number edit inherit its pricing context.
        contexts.extend(
            [" ".join(old[max(0, i - 6) : j + 6]), " ".join(new[max(0, a - 6) : b + 6])]
        )
    context = " \n ".join(contexts).lower()
    matched = [name for name, (_, pattern) in CONCEPTS.items() if re.search(pattern, context)]
    ratio = matcher.ratio()
    money = any(
        re.search(r"(?:[$€£]\s*\d|\d[\d.,]*\s*(?:USD|EUR|GBP|%))", c, re.I) for c in contexts
    )
    score = min(
        100,
        10 + round(20 * (1 - ratio)) + sum(CONCEPTS[k][0] for k in matched) + (20 if money else 0),
    )
    bounded = [{k: v[:500] for k, v in change.items()} for change in changes[:10]]
    excerpt = bounded[0]
    summary = f"{len(changes)} text change(s)"
    if matched:
        summary += f"; concepts: {', '.join(matched)}"
    summary += f". Before: {excerpt['removed'][:140] or '(none)'}. After: {excerpt['added'][:140] or '(none)'}."
    return {
        **base,
        "status": "changed",
        "changed": True,
        "importance_score": score,
        "similarity_ratio": round(ratio, 6),
        "matched_concepts": matched,
        "changes": bounded,
        "diff_truncated": bounded != changes,
        "change_summary": summary,
    }
