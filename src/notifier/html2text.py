"""HTML to readable plain text, with no third-party dependency.

Used to derive the ``text/plain`` alternative of an HTML email when the author
supplied only HTML. ``django.utils.html.strip_tags`` is not adequate for this:
it deletes tags without replacing them, so paragraphs run together, list items
concatenate, and **link targets vanish entirely** -- which is the one thing the
reader of a plaintext alternative most needs.

    <p>Track it <a href="https://x.example/1">here</a>.</p>
    <ul><li>2x Widget</li><li>1x Gadget</li></ul>

    strip_tags:  "Track it here.\\n2x Widget1x Gadget"
    this module: "Track it here (https://x.example/1).\\n\\n- 2x Widget\\n- 1x Gadget"

This is a pragmatic converter for transactional email, not a general-purpose
renderer: it does not wrap text, lay out column widths, or interpret CSS.
Authors who need exact plaintext should write ``body_text``, which always wins.
"""

import re
from html.parser import HTMLParser

#: Elements whose *content* is not text at all.
_SKIP = frozenset({"head", "noscript", "script", "style", "title"})

#: Elements that start and end a block, separated by a blank line.
_PARAGRAPH = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "dd",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)

#: Elements that merely start a new line.
_LINE = frozenset({"li", "tr"})

#: Elements separated from their siblings by a tab rather than a newline.
_CELL = frozenset({"td", "th"})

_PARAGRAPH_BREAK = "\n\n"
_LINE_BREAK = "\n"

#: Wraps <pre> content so the whitespace-collapsing pass leaves it alone.
_PROTECT = "\x00"

_TRAILING_SPACE = re.compile(r"[ \t]+\n")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")
_RUNS_OF_SPACE = re.compile(r"[ \t]{2,}")
_WHITESPACE = re.compile(r"\s+")
_PROTECTED = re.compile(f"{_PROTECT}(.*?){_PROTECT}", re.DOTALL)

#: hrefs that carry no information a reader could act on.
_USELESS_HREF = re.compile(r"^(#|javascript:|data:)", re.IGNORECASE)

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        #: (href, index into _parts where the anchor's text starts)
        self._links: list[tuple[str, int]] = []

    # -- collection -----------------------------------------------------

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        attributes = dict(attrs)

        if tag == "br":
            self._parts.append(_LINE_BREAK)
        elif tag == "hr":
            self._parts.append(f"{_PARAGRAPH_BREAK}---{_PARAGRAPH_BREAK}")
        elif tag == "img":
            alt = (attributes.get("alt") or "").strip()
            if alt:
                self._parts.append(f"[{alt}]")
        elif tag == "a":
            self._links.append((attributes.get("href") or "", len(self._parts)))
        elif tag == "li":
            self._parts.append(f"{_LINE_BREAK}- ")
        elif tag in _CELL:
            # Not before the first cell of a row, or every line starts indented.
            if not self._at_line_start():
                self._parts.append("\t")
        elif tag in _LINE:
            self._parts.append(_LINE_BREAK)
        elif tag in _PARAGRAPH:
            self._parts.append(_PARAGRAPH_BREAK)

        if tag == "pre":
            self._pre_depth += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        # A self-closing skip element has no content to skip, and HTMLParser
        # never calls handle_endtag for it -- so letting handle_starttag open a
        # skip region here would leave the depth counter stuck above zero and
        # silently discard the entire rest of the document.
        if tag in _SKIP or tag == "pre":
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)

        if tag == "a":
            self._close_link()
        elif tag in _LINE or tag in _CELL:
            pass  # the next sibling's opening tag supplies the separator
        elif tag in _PARAGRAPH:
            self._parts.append(_PARAGRAPH_BREAK)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._pre_depth:
            self._parts.append(f"{_PROTECT}{data}{_PROTECT}")
            return
        collapsed = _WHITESPACE.sub(" ", data)
        if collapsed:
            self._parts.append(collapsed)

    def _at_line_start(self) -> bool:
        for part in reversed(self._parts):
            if part:
                return part.endswith("\n")
        return True

    # -- links ----------------------------------------------------------

    def _close_link(self) -> None:
        if not self._links:
            return
        href, start = self._links.pop()
        self._append_target(href, "".join(self._parts[start:]).strip())

    def _append_target(self, href: str, label: str) -> None:
        if not href or _USELESS_HREF.match(href):
            return
        target = self._display_target(href)
        if not target:
            return
        # Skip when the label already shows where the link goes -- "Visit
        # example.com (https://example.com)" is noise, not help. Compared without
        # the scheme, so a label of "x.example/a" suppresses "https://x.example/a".
        if self._bare(target) in label or self._bare(href) in label:
            return
        self._parts.append(target if not label else f" ({target})")

    @staticmethod
    def _display_target(href: str) -> str:
        if href.lower().startswith("mailto:"):
            # The address is the useful part; the scheme is not.
            return href[len("mailto:") :]
        return href

    @staticmethod
    def _bare(href: str) -> str:
        return _SCHEME.sub("", href).rstrip("/")

    # -- output ---------------------------------------------------------

    def text(self) -> str:
        # An unclosed <a> would otherwise drop its target silently, and template
        # bodies are hand-edited.
        while self._links:
            href, start = self._links.pop()
            self._append_target(href, "".join(self._parts[start:]).strip())

        out = "".join(self._parts)
        out = _PROTECTED.sub(_collapse_around_protected, out)
        out = _TRAILING_SPACE.sub("\n", out)
        out = _EXTRA_BLANK_LINES.sub(_PARAGRAPH_BREAK, out)
        return "\n".join(line.rstrip() for line in out.splitlines()).strip()


def _collapse_around_protected(match: re.Match) -> str:
    """Keep <pre> content verbatim while the rest of the document is collapsed."""
    return match.group(1).replace(_PROTECT, "")


def html_to_text(html: str) -> str:
    """Convert ``html`` to plain text, keeping link targets and block structure."""
    if not html:
        return ""
    converter = _Converter()
    converter.feed(html)
    converter.close()
    return converter.text()
