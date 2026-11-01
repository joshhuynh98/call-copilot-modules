"""HTML to readable text, using only the standard library. Prefers the page's <main>/<article> content."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "header", "footer", "aside", "form", "button", "select"}
_BLOCK = {"p", "div", "li", "ul", "ol", "tr", "table", "pre", "br", "section", "article", "main", "blockquote",
          "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._main_depth = 0
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in _SKIP and tag not in _VOID:
            self._skip_depth += 1
        elif self._skip_depth == 0:
            if tag in ("main", "article") or dict(attrs).get("role") == "main":
                self._main_depth += 1
            if tag in _BLOCK:
                self._add("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif self._skip_depth == 0:
            if tag in ("main", "article") and self._main_depth:
                self._main_depth -= 1
            if tag in _BLOCK:
                self._add("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._skip_depth == 0:
            self._add(data)

    def _add(self, text: str) -> None:
        self.all_parts.append(text)
        if self._main_depth:
            self.main_parts.append(text)


def _clean(parts: list[str]) -> str:
    text = "".join(parts)
    lines = [re.sub(r"[ \t\r\f\v\xa0]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def extract_page(html: str) -> tuple[str, str]:
    """Returns (title, text). Uses the <main>/<article> text when it is substantial, else the whole body."""
    ex = _Extractor()
    ex.feed(html)
    ex.close()
    main, everything = _clean(ex.main_parts), _clean(ex.all_parts)
    return re.sub(r"\s+", " ", ex.title).strip(), main if len(main) >= 200 else everything
