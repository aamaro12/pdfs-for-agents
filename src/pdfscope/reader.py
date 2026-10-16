"""The reading steps an agent takes: outline, find, read, table and show.

Every result carries citations of the form p12 (a page) or p12:b3 (block 3 of page 12), with the page
label the PDF prints when it declares one.
"""

from __future__ import annotations

import math
import os
import re
import time

from .document import Document, Page
from .layout import Block, is_number

FIND_LIMIT = 20
READ_PAGES = 10


class ReadError(ValueError):
    pass


REF = re.compile(r"^p(?:age)?\s*(\d+)(?:\s*[:#.]\s*b(?:lock)?\s*(\d+))?$", re.I)


def parse_ref(ref: str) -> tuple[int, int | None]:
    m = REF.match(ref.strip())
    if not m:
        raise ReadError(f"not a page or block reference: {ref!r} (use p12 or p12:b3)")
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def parse_pages(spec: str, last: int) -> list[int]:
    """'12', 'p12', '12-14' or '3,7,9-10' as page numbers."""
    out = []
    for part in spec.replace("p", "").replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            start, end = int(a), int(b or a)
        except ValueError:
            raise ReadError(f"not a page range: {spec!r}") from None
        if not 1 <= start <= end <= last:
            raise ReadError(f"pages {part} are outside 1 to {last}")
        out += range(start, end + 1)
    if not out:
        raise ReadError(f"no pages in {spec!r}: give pages such as 12, 12-14 or 3,7")
    return out


def _block(doc: Document, ref: str) -> tuple[Page, int, Block]:
    number, index = parse_ref(ref)
    if not 1 <= number <= doc.pages:
        raise ReadError(f"page {number} is outside 1 to {doc.pages}")
    page = doc.page(number)
    if index is None:
        raise ReadError(f"{ref} is a page; give a block such as p{number}:b1")
    if not 1 <= index <= len(page.blocks):
        raise ReadError(f"p{number} has blocks b1 to b{len(page.blocks)}")
    return page, index, page.blocks[index - 1]


class _Budget:
    """Seconds to spend reading pages not read before. Once they are spent, such pages are left for another
    call, so OCR of a long scan runs over several calls instead of past a client's timeout. Every call reads
    at least one new page, and pages read before are always used."""

    def __init__(self, doc: Document, seconds: float | None):
        self.doc, self.seconds, self.start, self.new, self.unread = doc, seconds, time.monotonic(), 0, []

    def allows(self, number: int) -> bool:
        if self.seconds is None or self.doc.is_cached(number):
            return True
        if self.new and time.monotonic() - self.start >= self.seconds:
            self.unread.append(number)
            return False
        self.new += 1
        return True


# outline -----------------------------------------------------------------------------------------------


def outline(doc: Document, budget: float | None = None) -> dict:
    """Every page: how it was read, its headings and tables, and anything to watch for. With a budget in
    seconds, pages not read within it are listed under "unread" for the next call."""
    pages = []
    spend = _Budget(doc, budget)
    for n in range(1, doc.pages + 1):
        if not spend.allows(n):
            continue
        page = doc.page(n)
        headings = [(f"p{n}:b{k}", b.text) for k, b in enumerate(page.blocks, 1) if b.kind == "heading"][:4]
        tables = [
            {
                "cite": f"p{n}:b{k}",
                "rows": len(b.rows),
                "columns": max(map(len, b.rows)),
                "first_row": [c for c in b.rows[0] if c][:6],
            }
            for k, b in enumerate(page.blocks, 1)
            if b.kind == "table"
        ]
        first = next((b.text for b in page.blocks if b.kind in ("text", "list") and b.text), "")
        pages.append(
            {
                "page": n,
                "label": page.label,
                "source": page.source,
                "headings": headings,
                "tables": tables,
                "figures": [
                    {"cite": f"p{n}:b{k}", "caption": b.text, "source": (b.info or {}).get("source")}
                    for k, b in enumerate(page.blocks, 1)
                    if b.kind == "figure"
                ],
                "starts": first[:90],
                "warnings": page.warnings,
            }
        )
    sources = {}
    for p in pages:
        sources[p["source"]] = sources.get(p["source"], 0) + 1
    return {
        "file": os.path.basename(doc.path),
        "sha256": doc.sha256[:12],
        "pages": doc.pages,
        "sources": sources,
        "outline": pages,
        "unread": spend.unread,
    }


# find ---------------------------------------------------------------------------------------------------

SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "b": 1e9, "billion": 1e9}
QUERY_NUMBER = re.compile(
    r"^\(?([-−–]?)[$€£¥]?\s*(\d[\d,]*(?:\.\d+)?)\s*(%|k|m|mn|bn|b|thousand|million|billion)?\)?$", re.I
)


def number_query(query: str) -> tuple[float, float, bool] | None:
    """(value, tolerance, percent) for a figure as people quote it: 60,922, 60.9bn, $26.9 billion, 12.5%, (48)."""
    m = QUERY_NUMBER.match(query.strip().replace(" ", "") if len(query) < 30 else "")
    if not m:
        return None
    sign = -1 if m.group(1) or query.strip().startswith("(") else 1
    digits, unit = m.group(2), (m.group(3) or "").lower()
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    scale = SCALES.get(unit, 1)
    value = sign * float(digits.replace(",", "")) * scale
    return value, 0.5 * 10 ** (-decimals) * scale, unit == "%"


def cell_value(text: str) -> float | None:
    """A table cell as a number: brackets and minus signs are negative, dashes are nil."""
    t = text.strip().replace("−", "-").replace("–", "-").replace("—", "-")
    if t in ("-", "") or not is_number(t):
        return None
    body = re.sub(r"[()$€£¥%*†‡+-]", "", t).strip()
    if re.search(r"\d[\s\u00a0\u202f]+\d", body) and not re.fullmatch(
        r"\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?:\.\d+)?", body
    ):
        return None  # two figures in one cell, not one number with spaces between its thousands
    negative = t.startswith("(") or t.startswith("-")
    digits = re.sub(r"[^\d.]", "", t.replace(",", ""))
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if negative else v


def _numbers_in(text: str):
    # a figure starts after a space or bracket: the 2023 in 2019-2023 or the 19 in COVID-19 is not negative
    for m in re.finditer(r"(?<![\w.,])\(?[-−]?\d[\d,]*(?:\.\d+)?\)?%?", text):
        v = cell_value(m.group(0).rstrip("%"))
        if v is not None:
            yield m.group(0), v


def find(
    doc: Document, query: str, limit: int = FIND_LIMIT, pages: str | None = None, budget: float | None = None
) -> dict:
    """Blocks, table cells and text inside figures that contain the text, or figures that round to the number.
    With a budget in seconds, pages not read within it are listed under "unread" for the next call."""
    query = query.strip()
    if not query:
        raise ReadError("find needs some text or a number")
    if limit < 1:
        raise ReadError("limit must be at least 1")
    target = number_query(query)
    numbers = parse_pages(pages, doc.pages) if pages else range(1, doc.pages + 1)
    hits = []
    spend = _Budget(doc, budget)
    for n in numbers:
        if not spend.allows(n):
            continue
        page = doc.page(n)
        for k, b in enumerate(page.blocks, 1):
            cite = f"p{n}:b{k}"
            if b.kind == "table":
                headings = _column_headings(b.rows)
                for r, row in enumerate(b.rows):
                    for c, cell in enumerate(row):
                        if _matches(cell, query, target, cell=True):
                            hits.append(
                                {
                                    "cite": cite,
                                    "page": n,
                                    "label": page.label,
                                    "kind": "table cell",
                                    "text": cell,
                                    "row": row[0] if c else "",
                                    "column": headings[c],
                                    "at": [r + 1, c + 1],
                                }
                            )
            elif _matches(b.text, query, target):
                hits.append(
                    {
                        "cite": cite,
                        "page": n,
                        "label": page.label,
                        "kind": b.kind,
                        "text": _snippet(b.text, query, target),
                    }
                )
            if b.kind == "figure":  # a chart's axis and data labels are held by the figure, not the text
                for label in (b.info or {}).get("labels", []):
                    if _matches(label, query, target):
                        hits.append(
                            {"cite": cite, "page": n, "label": page.label, "kind": "figure label", "text": label}
                        )
            if len(hits) >= limit:
                return {"query": query, "hits": hits[:limit], "more": True, "unread": spend.unread}
    return {"query": query, "hits": hits, "more": False, "unread": spend.unread}


YEAR = re.compile(r"^(?:FY\s?)?(?:19|20)\d\d[A-Z]?$", re.I)


def _column_headings(rows: list[list[str]]) -> list[str]:
    """For each column, the headings above the first row of figures, nearest last. A row of years over the
    figures (2024, 2023, as OCR reads a table) is a heading row, not figures."""

    def figures(r):
        cells = [c for c in r[1:] if c]
        if cells and not r[0] and all(YEAR.match(c.strip()) for c in cells):
            return False
        return any(cell_value(c) is not None for c in r[1:])

    first = next((i for i, r in enumerate(rows) if figures(r)), 1)
    heads = []
    for c in range(max(map(len, rows))):
        parts = [r[c] for r in rows[:first] if c < len(r) and r[c]]
        heads.append(" · ".join(parts))
    return heads


def _matches(text: str, query: str, target, cell: bool = False) -> bool:
    if not text:
        return False
    if target is None:
        return query.lower() in text.lower()
    value, tol, percent = target
    candidates = [(text, cell_value(text))] if cell else list(_numbers_in(text))
    for _, v in candidates:
        if v is None:
            continue
        if value >= 0:
            v = abs(v)  # a figure asked for plainly matches it shown either way, such as (12) for a loss
        # a figure may be shown in units, thousands, millions or billions
        for scale in (1, 1e3, 1e6, 1e9):
            if math.isclose(v * scale, value, abs_tol=tol) or abs(v * scale - value) <= tol:
                return True
    return False


def _snippet(text: str, query: str, target) -> str:
    if target is None:
        i = text.lower().find(query.lower())
    else:
        i = next((m.start() for m in re.finditer(r"\d", text)), 0)
    return ("…" if i > 80 else "") + text[max(0, i - 80) : i + 160] + ("…" if i + 160 < len(text) else "")


# read ---------------------------------------------------------------------------------------------------


def read(doc: Document, pages: str, limit: int = READ_PAGES) -> dict:
    """Pages in reading order, every block tagged with its citation."""
    numbers = parse_pages(pages, doc.pages)
    if len(numbers) > limit:
        raise ReadError(f"{len(numbers)} pages asked for; read at most {limit} at a time")
    out = []
    for n in numbers:
        page = doc.page(n)
        blocks = [
            {
                "cite": f"p{n}:b{k}",
                "kind": b.kind,
                "text": b.text,
                "level": b.level,
                "rows": b.rows,
                "box": [round(v, 4) for v in b.box],
                "source": (b.info or {}).get("source"),
            }
            for k, b in enumerate(page.blocks, 1)
        ]
        out.append({"page": n, "label": page.label, "source": page.source, "warnings": page.warnings, "blocks": blocks})
    return {"file": os.path.basename(doc.path), "pages": out}


# table --------------------------------------------------------------------------------------------------

UNITS = re.compile(
    r"\b(?:in|amounts? in|figures? in|expressed in)\s+(?:[$€£¥]|usd|eur|gbp)?\s*(thousands?|millions?|billions?)\b",
    re.I,
)


def table(doc: Document, ref: str) -> dict:
    """One table: its cells, the figures parsed, and the units stated above it."""
    page, index, block = _block(doc, ref)
    if block.kind != "table":
        raise ReadError(f"{ref} is a {block.kind}, not a table")
    # stated in the table's own heading rows, as statements often do, or in the text just above it
    first = next((i for i, r in enumerate(block.rows) if any(cell_value(c) is not None for c in r[1:])), 1)
    texts = [" ".join(r) for r in block.rows[: max(first, 1)]] + [
        b.text or "" for b in reversed(page.blocks[: index - 1][-4:])
    ]
    units = None
    for text in texts:
        m = UNITS.search(text)
        if m:
            units = m.group(1).lower().rstrip("s") + "s"
            break
    values = [[cell_value(c) for c in row] for row in block.rows]
    return {
        "cite": ref,
        "page": page.number,
        "label": page.label,
        "units": units,
        "rows": block.rows,
        "values": values,
        "box": [round(v, 4) for v in block.box],
    }


# figure -------------------------------------------------------------------------------------------------


def figure(doc: Document, ref: str) -> dict:
    """What a figure shows: a short description, and for a chart the data as a table.

    Both come from small vision models (see figures.py), so they are drafts to check against `show`. Labels
    are the text the PDF itself holds inside the figure, which is exact.
    """
    from . import figures

    page, index, block = _block(doc, ref)
    if block.kind != "figure":
        raise ReadError(f"{ref} is a {block.kind}, not a figure")
    info = block.info or {}
    cached = doc.cached_figure(page.number, index, block.box)
    if cached is None:
        image = doc.pdf.render(page.number, 200)
        w, h = image.size
        x0, y0, x1, y1 = block.box
        crop = image.crop(
            (int(x0 * w), int(y0 * h), max(int(x0 * w) + 1, int(x1 * w)), max(int(y0 * h) + 1, int(y1 * h)))
        )
        try:
            description = figures.describe(crop)
            chart = figures.looks_like_chart(description, info.get("labels", []))
            table = figures.chart_table(crop) if chart else None
        except figures.Unavailable as e:
            raise ReadError(str(e)) from None
        cached = {
            "description": description,
            "chart": chart,
            "table": table,
            "models": [figures.DESCRIBER] + ([figures.CHART_READER] if chart else []),
        }
        doc.store_figure(page.number, index, block.box, cached)
    labels = info.get("labels", [])
    return {
        "cite": ref,
        "page": page.number,
        "label": page.label,
        "source": info.get("source"),
        "caption": block.text,
        "labels": labels,
        "labels_from": info.get("labels_from", "text layer"),
        **cached,
        "unconfirmed": _check_against_labels(cached["table"], labels) if cached["table"] else None,
        "note": "Description and table come from small vision models: check figures against show.",
    }


def _check_against_labels(table: list[list[str]], labels: list[str]) -> list[str] | None:
    """Figures in a chart's table that are not among the numbers printed on the chart, when it prints enough
    of them to check (charts with data labels). None when there are too few printed numbers to tell."""

    def plain(text):  # signs and currency symbols aside: "($4.1)" and "-4.1" are the same printed figure
        return re.sub(r"[$€£¥]", "", text)

    printed = {round(abs(v), 4) for t in labels for text, v in _numbers_in(plain(t)) if not YEAR.match(text)}
    printed -= _axis_ticks(printed)
    if len(printed) < 4:
        return None
    cells = [c for row in table[1:] for c in row[1:]]
    return [c for c in cells if (v := cell_value(plain(c))) is not None and round(abs(v), 4) not in printed]


def _axis_ticks(values: set[float]) -> set[float]:
    """Numbers that step evenly by a round amount (0, 100, 200, 300, or 0.5, 1.0, 1.5): an axis, not data."""
    ticks: set[float] = set()
    left = sorted(values)
    while True:
        best: list[float] = []
        for i, a in enumerate(left):
            for b in left[i + 1 :]:
                step = b - a
                mantissa = step / 10 ** math.floor(math.log10(step))
                if not any(math.isclose(mantissa, m) for m in (1, 2, 2.5, 5)):
                    continue
                if not math.isclose(a / step, round(a / step), abs_tol=1e-6):
                    continue
                run = [a]
                while (nxt := next((v for v in left if math.isclose(v, run[-1] + step)), None)) is not None:
                    run.append(nxt)
                if len(run) > len(best):
                    best = run
        if len(best) < 3:
            return ticks
        ticks |= set(best)
        left = [v for v in left if v not in best]


# show ---------------------------------------------------------------------------------------------------


def show(doc: Document, ref: str, out: str, dpi: int = 200) -> dict:
    """Save an image of a page or a block, for an agent that can see images or a person checking a citation."""
    number, index = parse_ref(ref)
    if not 1 <= number <= doc.pages:
        raise ReadError(f"page {number} is outside 1 to {doc.pages}")
    image = doc.pdf.render(number, dpi)
    box = None
    if index is not None:
        _, _, block = _block(doc, ref)
        x0, y0, x1, y1 = block.box
        pad = 0.01
        w, h = image.size
        box = (
            max(0, int((x0 - pad) * w)),
            max(0, int((y0 - pad) * h)),
            min(w, int((x1 + pad) * w)),
            min(h, int((y1 + pad) * h)),
        )
        image = image.crop(box)
    image.save(out)
    return {"cite": ref, "image": out, "width": image.size[0], "height": image.size[1]}
