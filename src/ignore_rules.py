"""Optional extraction filters; invalid customer rules are skipped safely."""

import logging
import re

from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError

log = logging.getLogger(__name__)


def filter_html(html: str, selectors: list[str], patterns: list[str]) -> str:
    if not selectors and not patterns:
        return html
    soup = BeautifulSoup(html, "html.parser")
    for index, selector in enumerate(selectors):
        try:
            for node in soup.select(selector):
                node.decompose()
        except (SelectorSyntaxError, ValueError, NotImplementedError):
            log.warning("Ignoring invalid CSS ignore selector at index %s.", index)
    for index, pattern in enumerate(patterns):
        try:
            compiled = re.compile(pattern)
        except re.error:
            log.warning("Ignoring invalid text ignore pattern at index %s.", index)
            continue
        for node in list(soup.find_all(string=True)):
            if node.parent and node.parent.name not in {"script", "style"}:
                node.replace_with(compiled.sub("", str(node)))
    return str(soup)
