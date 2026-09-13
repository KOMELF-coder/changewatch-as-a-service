"""Conservative monetary pairing with exact decimal arithmetic; no exchange rates."""

import re
from decimal import Decimal

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
    """Fallback: pair only unique, identical textual entity labels.

    Never match prices by their positions in a diff. A bare single price is
    supported only when every non-monetary token is unchanged.
    """
    old, new = tokens(previous), tokens(current)
    if sum(isinstance(t, tuple) for t in old) == sum(isinstance(t, tuple) for t in new) == 1:
        # With no DOM identity, changing a title/description may mean a different
        # product. A single price alone does not establish continuity.
        if [t for t in old if isinstance(t, str)] != [t for t in new if isinstance(t, str)]:
            return []

    def labelled(items):
        pairs, label = {}, []
        for token in items:
            if isinstance(token, tuple):
                key = " ".join(label).strip(" -:;|").casefold()
                pairs.setdefault(key, []).append(token)
                label = []
            else:
                label.append(token)
        return pairs

    before, after = labelled(old), labelled(new)
    changes = []
    for label in before:
        if label not in after:
            continue
        if len(before[label]) != 1 or len(after[label]) != 1:
            continue
        if not label and (
            len(before) != 1
            or len(after) != 1
            or [x for x in old if isinstance(x, str)] != [x for x in new if isinstance(x, str)]
        ):
            continue
        change = price_difference(before[label][0], after[label][0])
        if change:
            changes.append(change)
    return changes


def price_difference(before: tuple, after: tuple) -> dict | None:
    currency, old_price = before
    new_currency, new_price = after
    if currency != new_currency or old_price == new_price:
        return None
    difference = new_price - old_price
    percent = difference / old_price * 100 if old_price else None
    return {
        "old_price": float(old_price),
        "new_price": float(new_price),
        "currency": currency,
        "price_change_absolute": float(difference),
        "price_change_percent": float(round(percent, 2)) if percent is not None else None,
    }


def price_score_floor(change: dict) -> int:
    # Use original amounts, not the rounded output percentage, at tier boundaries.
    old = Decimal(str(change["old_price"]))
    difference = Decimal(str(change["new_price"])) - old
    percent = abs(difference / old * 100) if old else Decimal(0)
    return 90 if percent >= 20 else 80 if percent >= 10 else 70 if percent >= 5 else 60
