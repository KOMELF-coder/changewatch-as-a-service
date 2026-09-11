"""Conservative monetary pairing with exact decimal arithmetic; no exchange rates."""

import re
from decimal import Decimal
from difflib import SequenceMatcher

# Comma or dot decimals, plus conventional comma/dot/space thousands grouping.
NUMBER = r"(?:\d{1,3}(?:[ ,.\u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?"
CURRENCY = r"(?:[$€£]|\b(?:EUR|USD|GBP)\b)"
MONEY = re.compile(
    rf"(?P<prefix>{CURRENCY})\s*(?P<first>{NUMBER})(?![\d.,])"
    rf"|(?<![\w.,])(?P<last>{NUMBER})\s*(?P<suffix>{CURRENCY})",
    re.IGNORECASE,
)
CURRENCIES = {"€": "EUR", "$": "USD", "£": "GBP"}


def amount(value: str) -> Decimal:
    value = re.sub(r"\s", "", value)
    decimal = re.search(r"[.,](\d{1,2})$", value)
    if decimal:
        integer = re.sub(r"[.,]", "", value[: decimal.start()])
        return Decimal(integer + "." + decimal.group(1))
    return Decimal(re.sub(r"[.,]", "", value))


def tokens(text: str) -> list:
    """Keep each amount and currency together, including spaced suffix symbols."""
    result, end = [], 0
    for match in MONEY.finditer(text):
        result.extend(text[end : match.start()].split())
        currency = (match["prefix"] or match["suffix"]).upper()
        result.append((CURRENCIES.get(currency, currency), amount(match["first"] or match["last"])))
        end = match.end()
    result.extend(text[end:].split())
    return result


def detect_price_changes(previous: str, current: str) -> list[dict]:
    old, new = tokens(previous), tokens(current)
    changes = []
    for kind, i, j, a, b in SequenceMatcher(None, old, new, autojunk=True).get_opcodes():
        if kind != "replace":
            continue
        before = [token for token in old[i:j] if isinstance(token, tuple)]
        after = [token for token in new[a:b] if isinstance(token, tuple)]
        # Do not guess across additions/removals, currencies or ambiguous price lists.
        if len(before) != 1 or len(after) != 1 or before[0][0] != after[0][0]:
            continue
        currency, old_price = before[0]
        new_price = after[0][1]
        if old_price == new_price:
            continue
        difference = new_price - old_price
        percent = difference / old_price * 100 if old_price else None
        changes.append(
            {
                "old_price": float(old_price),
                "new_price": float(new_price),
                "currency": currency,
                "price_change_absolute": float(difference),
                "price_change_percent": float(round(percent, 2)) if percent is not None else None,
            }
        )
    return changes


def price_score_floor(change: dict) -> int:
    # Use original amounts, not the rounded output percentage, at tier boundaries.
    old = Decimal(str(change["old_price"]))
    difference = Decimal(str(change["new_price"])) - old
    percent = abs(difference / old * 100) if old else Decimal(0)
    return 90 if percent >= 20 else 80 if percent >= 10 else 70 if percent >= 5 else 60
