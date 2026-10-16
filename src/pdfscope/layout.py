"""From spans with boxes to blocks in reading order: headers and footers, tables, headings and paragraphs.

The same rules run on spans from the PDF text layer and on words read by OCR, so a scanned page and a
digital one come out in the same shape. All boxes are page fractions with the origin at the top left.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .pdf import Span

MARGIN = 0.10  # top and bottom bands where running headers, footers and page numbers sit
NUMBER = re.compile(r"^[\(\[]?[-–—−+]?[$€£¥]?\s?[-–—−]?\d[\d,.'  ]*%?[\)\]]?[%*†‡]?$|^[-–—]$|^n/?a$", re.I)
CURRENCY = re.compile(r"^[$€£¥]$")
PAGE_NUMBER = re.compile(r"^(page\s*)?[-–—]?\s*\d{1,4}\s*[-–—]?(\s*(of|/)\s*\d{1,4})?$|^[ivxlcdm]{1,6}$", re.I)
BULLET = re.compile(r"^([•◦▪■●○‣∙·\-–*]|\(?\d{1,2}[.)]|\(?[a-z][.)]|\([ivx]+\))\s+")


@dataclass
class Block:
    kind: str  # heading, text, list, table, figure, header, footer
    box: tuple[float, float, float, float]
    text: str = ""  # for a figure, its caption
    level: int = 0
    rows: list[list[str]] | None = None
    spans: list[Span] = field(default_factory=list)
    info: dict | None = None  # for a figure: where it came from and the text labels inside it


def is_number(text: str) -> bool:
    return bool(NUMBER.match(text.strip()))


def box_of(spans) -> tuple[float, float, float, float]:
    return (min(s.x0 for s in spans), min(s.y0 for s in spans), max(s.x1 for s in spans), max(s.y1 for s in spans))


# Rows ---------------------------------------------------------------------------------------------


def rows_of(spans: list[Span]) -> list[list[Span]]:
    """Group spans into visual rows (shared vertical band), each sorted left to right."""
    rows: list[list[Span]] = []
    for s in sorted(spans, key=lambda s: ((s.y0 + s.y1) / 2, s.x0)):
        mid = (s.y0 + s.y1) / 2
        if rows:
            r = rows[-1]
            top, bottom = min(x.y0 for x in r), max(x.y1 for x in r)
            if top <= mid <= bottom and abs(s.height - statistics.median(x.height for x in r)) < 0.6 * max(
                s.height, 1e-6
            ):
                r.append(s)
                continue
        rows.append([s])
    return [sorted(r, key=lambda s: s.x0) for r in rows]


# Headers and footers -------------------------------------------------------------------------------


def margin_key(text: str) -> str:
    """Text of a margin row with digits blanked, so "Page 4" on one page matches "Page 5" on the next."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.strip().lower()))


def margin_rows(rows: list[list[Span]]) -> list[list[Span]]:
    return [r for r in rows if max(s.y1 for s in r) < MARGIN or min(s.y0 for s in r) > 1 - MARGIN]


def margins(rows: list[list[Span]], repeated: set[str] | None = None):
    """Split rows (top to bottom) into (header rows, body rows, footer rows).

    Candidates are the rows in the top and bottom tenth of the page. One is a running header or footer
    when it is a page number, when the same text sits in the margins of nearby pages (`repeated`), or,
    with no nearby pages to compare, when it sits at the very edge or is set apart from the body by a
    gap, and is not set in large type like a title.
    """
    if not rows:
        return [], [], []
    body_h = statistics.median(s.height for r in rows for s in r)

    def text(r):
        return " ".join(s.text for s in r)

    def running(r, gap, edge):
        t = text(r)
        page_number = bool(PAGE_NUMBER.match(t))
        if page_number:
            return True
        if repeated is not None:
            return margin_key(t) in repeated
        big = statistics.median(s.height for s in r) > 1.5 * body_h
        bold = all(s.bold for s in r)
        words = len(t.split())
        return not big and ((edge and words <= 25) or (gap > 1.2 * body_h and words <= 25 and not bold))

    top = []
    for k, r in enumerate(rows):
        if max(s.y1 for s in r) >= 0.10 or k >= 4:
            break
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        gap = (min(s.y0 for s in nxt) - max(s.y1 for s in r)) if nxt else 1
        if not running(r, gap, max(s.y1 for s in r) < 0.06):
            break
        top.append(k)
    bottom = []
    for k in range(len(rows) - 1, -1, -1):
        r = rows[k]
        if min(s.y0 for s in r) <= 0.90 or len(rows) - k > 4 or k in top:
            break
        prv = rows[k - 1] if k > 0 else None
        gap = (min(s.y0 for s in r) - max(s.y1 for s in prv)) if prv else 1
        if not running(r, gap, min(s.y0 for s in r) > 0.94):
            break
        bottom.append(k)
    header = [rows[k] for k in top]
    footer = [rows[k] for k in sorted(bottom)]
    body = [r for k, r in enumerate(rows) if k not in set(top) | set(bottom)]
    return header, body, footer


# Tables ---------------------------------------------------------------------------------------------


def _clean(row: list[Span]) -> list[Span]:
    """Drop currency signs that sit in their own span: they belong to the figure beside them."""
    return [s for s in row if not CURRENCY.match(s.text.strip())]


def _tabular(row: list[Span]) -> bool:
    """A row that looks like table cells: figures after a label, or several short cells. Prose is not."""
    if len(row) < 2:
        return False
    words = [len(s.text.split()) for s in row]
    numbers = sum(1 for s in row[1:] if is_number(s.text))
    if numbers >= 1 and max(words[1:]) <= 6:
        return True
    return len(row) >= 3 and max(words) <= 5


def _aligned(rows: list[list[Span]], i: int) -> int:
    """End of a run of at least 3 rows from i with the same number (2 or more) of short, left-aligned cells."""
    k = len(rows[i])
    if k < 2 or max(len(s.text.split()) for s in rows[i]) > 6:
        return i
    j = i + 1
    while j < len(rows) and len(rows[j]) == k and max(len(s.text.split()) for s in rows[j]) <= 6:
        if any(abs(rows[j][c].x0 - rows[i][c].x0) > 0.02 for c in range(k)):
            break
        if min(s.y0 for s in rows[j]) - max(s.y1 for s in rows[j - 1]) > 2.5 * rows[j][0].height:
            break
        j += 1
    if j - i < 3:
        return i
    cells = [s.text.strip() for r in rows[i:j] for s in r]
    words = statistics.mean(len(c.split()) for c in cells)
    continuing = sum(1 for c in cells if c[:1].islower()) / len(cells)
    return j if words <= 3.5 and continuing <= 0.25 else i  # narrow columns of prose are not tables


def find_tables(rows: list[list[Span]]) -> list[tuple[int, int]]:
    """Index ranges [start, end) of rows that form tables: figures in columns, or aligned short cells."""
    found = _numeric_tables(rows)
    taken = {k for a, b in found for k in range(a, b)}
    i = 0
    while i < len(rows):
        if i in taken:
            i += 1
            continue
        end = _aligned(rows, i)
        if end > i and not any(k in taken for k in range(i, end)):
            start = i - 1 if i > 0 and (i - 1) not in taken and len(rows[i - 1]) == len(rows[i]) else i
            found.append((start, end))
            taken |= set(range(start, end))
            i = end
        else:
            i += 1
    return sorted(found)


def _numeric_tables(rows: list[list[Span]]) -> list[tuple[int, int]]:
    if not rows:
        return []
    heights = [statistics.median(s.height for s in r) for r in rows]
    tables, i = [], 0
    while i < len(rows):
        if not _tabular(_clean(rows[i])):
            i += 1
            continue
        j, last, tab = i + 1, i, 1
        left = min(s.x0 for s in rows[i])
        while j < len(rows):
            gap = min(s.y0 for s in rows[j]) - max(s.y1 for s in rows[j - 1])
            if gap > 2.5 * max(heights[j], heights[j - 1]):
                break
            r = _clean(rows[j])
            if _tabular(r):
                last, tab = j, tab + 1
            elif not (len(r) == 1 and abs(r[0].x0 - left) < 0.08 and len(r[0].text.split()) <= 12):
                break  # a full text line ends the table; short labels (section rows) may sit inside it
            j += 1
        end = last + 1
        cells = [len(s.text.split()) for r in rows[i:end] for s in r]
        phrases_ = [s.text.strip() for r in rows[i:end] for s in r if len(s.text.split()) >= 3]
        # phrases that start in lower case carry a sentence on from the line before: prose, not a table
        continuing = sum(1 for c in phrases_ if c[:1].islower()) / max(1, len(phrases_)) if len(phrases_) >= 3 else 0
        shaped = tab >= 3 or (tab >= 2 and max(len(_clean(r)) for r in rows[i:end]) >= 3)
        if shaped and statistics.mean(cells) <= 6 and continuing <= 0.3:
            start = max(_header_start(rows, i, heights), tables[-1][1] if tables else 0)
            tables.append((start, end))
            i = end
        else:
            i += 1
    return tables


def _header_start(rows, i, heights) -> int:
    """Include up to 3 rows of column headings directly above the first data row."""
    start = found = i
    body_x0 = min(s.x0 for r in rows[i : i + 3] for s in r[1:]) if any(len(r) > 1 for r in rows[i : i + 3]) else 1
    left = min(s.x0 for s in rows[i])
    while start > 0 and i - start < 5:
        r = rows[start - 1]
        gap = min(s.y0 for s in rows[start]) - max(s.y1 for s in r)
        text = " ".join(s.text for s in r)
        if gap > 2 * heights[start] or len(text.split()) > 20 or (len(r) == 1 and r[0].x1 - r[0].x0 > 0.6):
            break
        if any(s.x1 > body_x0 for s in r):
            found = start - 1  # column headings over the figures
        elif not (len(r) == 1 and abs(r[0].x0 - left) < 0.08 and len(text.split()) <= 8):
            break  # neither headings nor a short section label
        start -= 1
    return found


def _gutters(rows: list[list[Span]], aspect: float) -> list[float]:
    """x positions of column gutters: strips that (almost) no word crosses in any row."""
    words = [w for r in rows for w in r]
    if len(rows) < 2 or not words:
        return []
    bins = 1000
    occ = [0] * bins
    for r in rows:
        cov = bytearray(bins)
        for w in r:
            lo, hi = max(0, int(w.x0 * bins)), min(bins - 1, int(w.x1 * bins))
            cov[lo : hi + 1] = b"\x01" * (hi - lo + 1)
        for k in range(bins):
            occ[k] += cov[k]
    min_width = 0.5 * statistics.median(w.height for w in words) * aspect
    tol = int(0.1 * len(rows))
    lo, hi = int(min(w.x0 for w in words) * bins), min(bins - 1, int(max(w.x1 for w in words) * bins))
    edges, k = [], lo + 1
    while k < hi:
        if occ[k] <= tol:
            j = k
            while j < hi and occ[j] <= tol:
                j += 1
            if (j - k) / bins >= min_width and j < hi:
                edges.append((k + j) / 2 / bins)
            k = j
        else:
            k += 1
    return edges


MONTH = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?$", re.I)
YEAR_END = re.compile(r"^(\d{4}|(fy|cy|q[1-4])\s?'?\d{2,4}|\d{4}[/-]\d{2,4})[a-z]?[,;:)*]?$", re.I)


def _heading_units(words: list[Span]) -> list[list[Span]]:
    """Column headings that ran together, split where a year ends one and a month or year starts the next."""
    units, cur = [], []
    for w in words:
        if cur and MONTH.match(w.text) and not MONTH.match(cur[-1].text):
            units.append(cur)
            cur = []
        cur.append(w)
        if YEAR_END.match(w.text):
            units.append(cur)
            cur = []
    if cur:
        units.append(cur)
    return units


def grid(rows: list[list[Span]], aspect: float = 1.29) -> list[list[str]]:
    """Cells of a table. Columns come from gutters between the words of its data rows; headings and
    section labels are placed by where their centre falls."""
    rows = [r for r in (_clean(r) for r in rows) if r]
    first = next((i for i, r in enumerate(rows) if _tabular(r)), 0)
    data = [i for i in range(first, len(rows)) if len(rows[i]) >= 2]

    def words(r):
        return [w for s in r for w in (s.words or [s]) if not CURRENCY.match(w.text.strip())]

    edges = _gutters([words(rows[i]) for i in data], aspect)

    def col(x):
        return sum(1 for e in edges if x > e)

    # where each column's figures sit, to place headings centred over them
    centres = [[] for _ in range(len(edges) + 1)]
    for i in data:
        for w in words(rows[i]):
            centres[col((w.x0 + w.x1) / 2)].append((w.x0 + w.x1) / 2)
    centres = [statistics.mean(c) if c else None for c in centres]

    def nearest(x):
        known = [(abs(x - c), k) for k, c in enumerate(centres) if c is not None]
        return min(known)[1] if known else col(x)

    out = []
    for i, r in enumerate(rows):
        cells = [[] for _ in range(len(edges) + 1)]
        if i in data:
            for w in sorted(words(r), key=lambda w: w.x0):
                cells[col((w.x0 + w.x1) / 2)].append(w.text)
        elif len(r) > 1 or col(r[0].x0 + 0.001) > 0:
            # column headings: split into units ending at a year, so "Jan 28, 2024 Jan 29, 2023" becomes two,
            # and put each over the nearest column of figures
            for unit in _heading_units(sorted(words(r), key=lambda w: w.x0)):
                x = (unit[0].x0 + unit[-1].x1) / 2
                c = col(x)
                cells[c if c == 0 else nearest(x)].append(" ".join(w.text for w in unit))
        else:
            cells[0].append(r[0].text)  # a section label in the first column
        out.append([tidy(" ".join(c)) for c in cells])
    keep = [j for j in range(len(edges) + 1) if any(r[j] for r in out)]
    return [[r[j] for j in keep] for r in out]


def tidy(cell: str) -> str:
    """A cell without loose currency signs, and figures without trailing OCR specks."""
    cell = re.sub(r"(?<!\S)[$€£¥](?!\S)", "", cell)
    cell = re.sub(r"\s+", " ", cell).strip()
    m = re.match(r"^(.*\d[\d,.]*\)?%?)[_|.,;:'`]+$", cell)
    if m and is_number(m.group(1)):
        cell = m.group(1)
    return cell


def table_from_cells(cells: list[dict]) -> list[list[str]]:
    """A grid from OCR table cells with row and column spans (Apple Vision's output)."""
    n_rows = max(c["rows"][1] for c in cells) + 1
    n_cols = max(c["cols"][1] for c in cells) + 1
    g = [[""] * n_cols for _ in range(n_rows)]
    for c in cells:
        g[c["rows"][0]][c["cols"][0]] = tidy(c["text"])
    keep = [j for j in range(n_cols) if any(r[j] for r in g)]
    return [[r[j] for j in keep] for r in g if any(r[j] for j in keep)]


# Ruled tables -----------------------------------------------------------------------------------------


def _merge_lines(lines, tol=0.003):
    """Join ruling lines that sit at the same position (drawn twice, or in segments)."""
    out = []
    for pos, a, b in sorted(lines):
        if out and abs(pos - out[-1][0]) < tol and a <= out[-1][2] + 0.01:
            out[-1] = (out[-1][0], min(out[-1][1], a), max(out[-1][2], b))
        else:
            out.append((pos, a, b))
    return out


def ruled_tables(spans: list[Span], horizontal, vertical, aspect: float = 1.29):
    """Tables marked by drawn ruling lines: [(box, rows, spans used)].

    Horizontal rules that line up form a table region; vertical rules inside it (or, when there are
    none, gutters between words) give the columns; the rules give the rows, with every text line
    between two rules joined into one row so cells can hold several lines.
    """
    hs = _merge_lines(horizontal)
    tables, used = [], set()
    groups, cur = [], []
    for line in sorted(hs):
        if cur and (
            line[0] - cur[-1][0] > 0.2
            or min(line[2], cur[0][2]) - max(line[1], cur[0][1]) < 0.5 * (cur[0][2] - cur[0][1])
        ):
            groups.append(cur)
            cur = []
        cur.append(line)
    if cur:
        groups.append(cur)
    for g in groups:
        if len(g) < 2:
            continue
        x0, x1 = min(l[1] for l in g) - 0.005, max(l[2] for l in g) + 0.005
        y0, y1 = g[0][0], g[-1][0]
        inside = [
            s for s in spans if id(s) not in used and x0 <= (s.x0 + s.x1) / 2 <= x1 and y0 < (s.y0 + s.y1) / 2 < y1
        ]
        text_rows = rows_of(inside)
        if len(text_rows) < 2:
            continue
        if _underlines(spans, used, inside, text_rows, x0, y0, y1):
            continue  # the rows are read as a table of figures, with their labels, instead
        vs = [
            v
            for v in _merge_lines(vertical, 0.004)
            if x0 + 0.01 < v[0] < x1 - 0.01 and min(v[2], y1) - max(v[1], y0) > 0.4 * (y1 - y0)
        ]
        if len(vs) >= 1:
            edges = sorted(v[0] for v in vs)
        else:
            words = [[w for s in r for w in (s.words or [s])] for r in text_rows]
            edges = _gutters(words, aspect)
        if not edges:
            continue
        cuts = [l[0] for l in g]
        bands = [(a, b) for a, b in zip(cuts, cuts[1:])]
        # with only a top, middle and bottom rule (booktabs style), each text line between them is a row
        by_band = [[r for r in text_rows if a < (r[0].y0 + r[0].y1) / 2 < b] for a, b in bands]
        filled = [b for b in by_band if b]
        # rules between most rows: every line between two rules is one row (cells may wrap);
        # few rules (top, middle and bottom only): each line is a row, wrapped lines fold into the row above
        per_row = len(filled) >= 0.6 * len(bands) and sum(map(len, filled)) <= 2.5 * len(filled)
        rows_out = []

        def cells_of(lines):
            cells = [[] for _ in range(len(edges) + 1)]
            for line in lines:
                for sp in line:
                    for w in sp.words or [sp]:
                        cells[sum(1 for e in edges if (w.x0 + w.x1) / 2 > e)].append(w.text)
            return [" ".join(c) for c in cells]

        for band_rows in filled:
            lines = [cells_of([line]) for line in band_rows]
            counts = [sum(1 for c in cells if c) for cells in lines]
            if len(lines) > 1 and all(n >= max(2, 0.67 * counts[0]) for n in counts):
                rows_out += lines  # lines that each fill the columns are rows of their own, not a wrapped cell
                continue
            if per_row:
                rows_out.append(cells_of(band_rows))
                continue
            for line in band_rows:
                cells = cells_of([line])
                if rows_out and not cells[0] and sum(1 for c in cells if c) < sum(1 for c in rows_out[-1] if c):
                    rows_out[-1] = [(a + " " + b).strip() for a, b in zip(rows_out[-1], cells)]
                else:
                    rows_out.append(cells)
        rows_out = [[tidy(c) for c in r] for r in rows_out]
        keep = [j for j in range(len(edges) + 1) if any(r[j] for r in rows_out)]
        rows_out = [[r[j] for j in keep] for r in rows_out if any(r[j] for j in keep)]
        if len(keep) < 2 or len(rows_out) < 2:
            continue
        used |= {id(s) for s in inside}
        tables.append(((x0, y0, x1, y1), rows_out, inside))
    return tables


def _underlines(spans, used, inside, text_rows, x0, y0, y1) -> bool:
    """Whether rules drawn only under columns of figures are a statement's subtotal lines rather than a grid:
    the rows between them are mostly figures, and most carry a label to the left of the rules. A column of
    prose beside a table (lines of many words, all about as wide) is not labels."""
    words = [w for s in inside for w in (s.words or [s])]
    figures = [w for w in words if is_number(w.text) or CURRENCY.match(w.text)]
    if len(figures) < 0.6 * len(words):
        return False
    left = [s for s in spans if id(s) not in used and s.x1 <= x0 + 0.01 and y0 < (s.y0 + s.y1) / 2 < y1]
    labels = rows_of(left)
    if not labels:
        return False
    widths = [max(s.x1 for s in r) - min(s.x0 for s in r) for r in labels]
    sizes = [sum(len(s.text.split()) for s in r) for r in labels]
    if len(labels) >= 3 and statistics.median(widths) >= 0.8 * max(widths) and statistics.median(sizes) >= 5:
        return False

    def labelled(r):
        cy, h = (r[0].y0 + r[0].y1) / 2, r[0].height
        return any(abs((s.y0 + s.y1) / 2 - cy) < 0.5 * h for label in labels for s in label)

    return sum(map(labelled, text_rows)) >= 0.5 * len(text_rows)


# Reading order ---------------------------------------------------------------------------------------


def _bbox(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _gutter(boxes, region) -> float | None:
    """x of a column gutter: the widest vertical strip that items barely cross (a page number or a centred
    line may), with content on both sides. Items that cross it are read as dividers between sections."""
    x0, y0, x1, y1 = region
    width = x1 - x0
    if width <= 0 or len(boxes) < 4:
        return None
    bins = 400
    cover = [0.0] * bins
    for b in boxes:
        if b[2] - b[0] >= 0.55 * width:
            continue  # titles and full-width lines do not hide a gutter
        lo = int((b[0] - x0) / width * (bins - 1))
        hi = int((b[2] - x0) / width * (bins - 1))
        for k in range(max(0, lo), min(bins, hi + 1)):
            cover[k] += b[3] - b[1]
    filled = max(cover) or 1
    total = sum(b[3] - b[1] for b in boxes)
    best, k = None, int(0.12 * bins)
    while k < int(0.88 * bins):
        if cover[k] <= 0.05 * filled:
            j = k
            while j < int(0.88 * bins) and cover[j] <= 0.05 * filled:
                j += 1
            cut = x0 + (k + j) / 2 / bins * width
            left = sum(b[3] - b[1] for b in boxes if b[2] <= cut)
            right = sum(b[3] - b[1] for b in boxes if b[0] >= cut)
            if j - k >= 2 and left > 0.2 * total and right > 0.2 * total and (best is None or j - k > best[0]):
                best = (j - k, cut)
            k = j
        else:
            k += 1
    return best[1] if best else None


def reading_order(items: list, box=lambda b: b.box, depth: int = 0) -> list[list]:
    """Items grouped into runs in reading order: columns read top to bottom, left before right.

    Columns are found from a vertical gutter that no narrow item crosses. Wide items (titles, full-width
    paragraphs, tables) cut the columns into sections, so a page with a title, two columns and a
    full-width footnote reads title, left column, right column, footnote.
    """
    if not items:
        return []
    boxes = [box(i) for i in items]
    region = _bbox(boxes)
    cut = _gutter(boxes, region) if depth < 8 and len(items) > 3 else None
    if cut is not None:
        crossing = [k for k, b in enumerate(boxes) if b[0] < cut < b[2]]
        others = [k for k in range(len(items)) if k not in set(crossing)]
        out, done = [], set()
        bands = sorted(crossing, key=lambda k: boxes[k][1])
        edges = [region[1] - 1] + [boxes[k][1] for k in bands] + [region[3] + 1]
        for n in range(len(edges) - 1):
            section = [k for k in others if k not in done and (boxes[k][1] + boxes[k][3]) / 2 < edges[n + 1]]
            done |= set(section)
            left = [items[k] for k in section if (boxes[k][0] + boxes[k][2]) / 2 < cut]
            right = [items[k] for k in section if (boxes[k][0] + boxes[k][2]) / 2 >= cut]
            out += reading_order(left, box, depth + 1) + reading_order(right, box, depth + 1)
            if n < len(bands):
                out.append([items[bands[n]]])
        return [run for run in out if run]
    # no columns: split where there is a clear horizontal gap, else read row by row
    heights = sorted(b[3] - b[1] for b in boxes)
    line = heights[len(heights) // 2]
    ys = sorted(range(len(items)), key=lambda k: boxes[k][1])
    runs, cur, reach = [], [ys[0]], boxes[ys[0]][3]
    for k in ys[1:]:
        if boxes[k][1] > reach + 1.2 * line:
            runs.append(cur)
            cur = []
        cur.append(k)
        reach = max(reach, boxes[k][3])
    runs.append(cur)
    if len(runs) > 1 and depth < 8:
        out = []
        for r in runs:
            out += reading_order([items[k] for k in r], box, depth + 1)
        return out
    return [[items[k] for k in sorted(ys, key=lambda k: (boxes[k][1], boxes[k][0]))]]


# Page layout -------------------------------------------------------------------------------------------


def layout(
    spans: list[Span],
    ocr_tables: list[dict] | None = None,
    repeated: set[str] | None = None,
    aspect: float = 1.29,
    rules=None,
    figures: list[dict] | None = None,
) -> list[Block]:
    """Blocks of a page in reading order. `repeated` holds margin texts seen on nearby pages (see margins);
    `rules` holds the page's ruling lines (see PDF.rules), which mark tables; `figures` holds images and
    drawings as {"box", "source"}, which become figure blocks."""
    spans = [s for s in spans if s.text.strip()]
    blocks: list[Block] = []
    for f in figures or []:
        x0, y0, x1, y1 = f["box"]
        pad = 0.025 if f["source"] == "drawing" else 0.0
        inside = [
            s
            for s in spans
            if x0 - pad <= (s.x0 + s.x1) / 2 <= x1 + pad
            and y0 - pad <= (s.y0 + s.y1) / 2 <= y1 + pad
            # a line just outside the drawing that carries on a paragraph above or below it is not a label
            and (
                (x0 <= (s.x0 + s.x1) / 2 <= x1 and y0 <= (s.y0 + s.y1) / 2 <= y1)
                or not _in_paragraph(s, spans, f["box"])
            )
        ]
        words = [len(sp.text.split()) for sp in inside]
        label_like = len(inside) <= 80 and (not words or (statistics.mean(words) <= 3.5 and max(words) <= 10))
        if f["source"] == "drawing" and (not label_like or (x1 - x0) * (y1 - y0) > 0.5):
            continue  # shapes behind running text (shading, decoration) are not a chart
        if label_like and (f["source"] == "drawing" or f.get("absorb")):
            # a chart's axis labels and data labels belong to the chart, not to the text around it
            ids = {id(s) for s in inside}
            spans = [s for s in spans if id(s) not in ids]
            if rules:
                inside_box = lambda a, b, c, d: x0 - pad <= a and b <= x1 + pad and y0 - pad <= c and d <= y1 + pad  # noqa: E731
                rules = (
                    [r for r in rules[0] if not inside_box(r[1], r[2], r[0], r[0])],
                    [r for r in rules[1] if not inside_box(r[0], r[0], r[1], r[2])],
                )
        labels = [s.text for s in sorted(inside, key=lambda s: (round(s.y0, 3), s.x0))]
        info = {"source": f["source"], "labels": labels, "labels_from": f.get("labels_from", "text layer")}
        box = tuple(f["box"])
        if f["source"] == "drawing" and inside:  # the box takes in the axis and data labels around the drawing
            lx0, ly0, lx1, ly1 = box_of(inside)
            box = (min(box[0], lx0), min(box[1], ly0), max(box[2], lx1), max(box[3], ly1))
        blocks.append(Block("figure", box, info=info))
    if not spans and not blocks:
        return []
    for t in ocr_tables or []:
        rows = table_from_cells(t["cells"])
        words = [len(c.split()) for r in rows for c in r if c]
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2 and words and statistics.mean(words) <= 12:
            x0, y0, x1, y1 = t["box"]
            inside = [
                s
                for s in spans
                if x0 - 0.01 <= (s.x0 + s.x1) / 2 <= x1 + 0.01 and y0 - 0.01 <= (s.y0 + s.y1) / 2 <= y1 + 0.01
            ]
            spans = [s for s in spans if s not in inside]
            blocks.append(Block("table", tuple(t["box"]), rows=rows, spans=inside))
    if rules:
        for box, rows, inside in ruled_tables(spans, *rules, aspect=aspect):
            ids = {id(s) for s in inside}
            spans = [s for s in spans if id(s) not in ids]
            blocks.append(Block("table", box, rows=rows, spans=inside))
    header, body, footer = margins(rows_of(spans), repeated)
    for kind, group in (("header", header), ("footer", footer)):
        for r in group:
            blocks.append(Block(kind, box_of(r), " ".join(s.text for s in r), spans=r))
    for start, end in find_tables(body):
        rs = [r for r in body[start:end] if r]
        flat = [s for r in rs for s in r]
        if len(rs) < 2:
            continue
        blocks.append(Block("table", box_of(flat), rows=grid(rs, aspect), spans=flat))
        for r in rs:
            r.clear()
    text_spans = [s for r in body for s in r]
    body_h = statistics.median(s.height for s in text_spans) if text_spans else 0.01
    out = [b for b in blocks if b.kind == "header"]
    # images stay out of the column analysis (a photo can straddle a gutter); they are placed by position after
    images = [b for b in blocks if b.kind == "figure" and (b.info or {}).get("source") != "drawing"]
    ordered = [b for b in blocks if b.kind == "table" or (b.kind == "figure" and b not in images)]
    for run in reading_order(ordered + text_spans):
        tables = [i for i in run if isinstance(i, Block)]
        spans_in_run = [i for i in run if isinstance(i, Span)]
        if not tables:
            out += _paragraphs(spans_in_run, body_h)
            continue
        # a run mixing tables and text: interleave by vertical position
        pieces = [(t.box[1], [t]) for t in tables]
        for para in _paragraphs(spans_in_run, body_h):
            pieces.append((para.box[1], [para]))
        for _, ps in sorted(pieces, key=lambda p: p[0]):
            out += ps
    for img in images:
        # before the first block below it in its own column, or after the last block above it there
        column = [
            k for k, b in enumerate(out) if b.kind != "header" and b.box[0] < img.box[2] and img.box[0] < b.box[2]
        ]
        below = [k for k in column if out[k].box[1] >= img.box[1]]
        if below:
            at = below[0]
        elif column:
            at = column[-1] + 1
        else:
            at = next((k for k, b in enumerate(out) if b.kind != "header" and b.box[1] >= img.box[1]), len(out))
        out.insert(at, img)
    out += [b for b in blocks if b.kind == "footer"]
    _captions(out)
    return out


def _in_paragraph(s: Span, spans: list[Span], box) -> bool:
    """Whether a span is a line of running text: long, or set just below or above another line that starts
    at the same place and lies outside the figure's box."""
    if len(s.text.split()) > 8:
        return True
    x0, y0, x1, y1 = box
    for t in spans:
        if t is s or abs(t.x0 - s.x0) > 0.01 or (x0 <= (t.x0 + t.x1) / 2 <= x1 and y0 <= (t.y0 + t.y1) / 2 <= y1):
            continue
        if 0 <= s.y0 - t.y1 < s.height or 0 <= t.y0 - s.y1 < s.height:
            return True
    return False


CAPTION = re.compile(r"^(figure|fig\.|chart|exhibit|graph|graphic|illustration|image|photo|plate|map|source)\b", re.I)


def _captions(blocks: list[Block]) -> None:
    """Give each figure the caption next to it: a short block just below or above that reads like one."""
    for k, b in enumerate(blocks):
        if b.kind != "figure":
            continue
        nearby = [
            blocks[j]
            for j in (k + 1, k - 1, k + 2, k - 2, k + 3)
            if 0 <= j < len(blocks) and blocks[j].kind in ("text", "heading") and blocks[j].text
        ]
        nearby = [c for c in nearby if min(abs(c.box[1] - b.box[3]), abs(b.box[1] - c.box[3])) < 0.08]
        named = [c for c in nearby if CAPTION.match(c.text)]
        short = [c for c in nearby if len(c.text.split()) <= 12 and not is_number(c.text)]
        if named or short:
            b.text = (named or short)[0].text
        labels = (b.info or {}).get("labels") or []
        if not b.text and labels and len(labels[0].split()) >= 3:
            b.text = labels[0]  # a title drawn inside the image


def _paragraphs(spans: list[Span], body_h: float) -> list[Block]:
    """Group consecutive lines of one column into headings, list items and paragraphs."""
    out: list[Block] = []
    lines = rows_of(spans)
    cur: list[list[Span]] = []

    def flush():
        if not cur:
            return
        flat = [s for line in cur for s in line]
        text = _join([" ".join(s.text for s in line) for line in cur])
        h = statistics.median(s.height for s in flat)
        words = len(text.split())
        if h > 1.3 * body_h and words <= 20:
            out.append(Block("heading", box_of(flat), text, level=1 if h > 1.9 * body_h else 2, spans=flat))
        elif all(s.bold for s in flat) and words <= 12 and len(cur) <= 2:
            out.append(Block("heading", box_of(flat), text, level=3, spans=flat))
        elif BULLET.match(text):
            out.append(Block("list", box_of(flat), BULLET.sub("", text, count=1), spans=flat))
        else:
            out.append(Block("text", box_of(flat), text, spans=flat))
        cur.clear()

    prev = None
    for line in lines:
        text = " ".join(s.text for s in line)
        if prev is not None:
            p_bottom = max(s.y1 for s in prev)
            gap = min(s.y0 for s in line) - p_bottom
            h = max(statistics.median(s.height for s in line), statistics.median(s.height for s in prev))
            size_change = (
                abs(statistics.median(s.height for s in line) - statistics.median(s.height for s in prev)) > 0.25 * h
            )
            bold_change = all(s.bold for s in prev) != all(s.bold for s in line)
            new_group = line[0].group >= 0 and prev[0].group >= 0 and line[0].group != prev[0].group
            if gap > 0.7 * h or size_change or bold_change or new_group or BULLET.match(text):
                flush()
        cur.append(line)
        prev = line
    flush()
    return out


JOINERS = {"a", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def _join(lines: list[str]) -> str:
    """Join wrapped lines, mending words hyphenated across a line break."""
    out = ""
    for line in lines:
        line = line.strip()
        if out.endswith("-") and len(out) > 1 and out[-2].isalnum() and line:
            stem, after = out.split()[-1][:-1], line.split()[0].rstrip("-").split("-")
            # a word broken at the line end is mended (busi- ness); a compound keeps its hyphen: one already
            # hyphenated (order-of-), an acronym or figure (non- GAAP, COVID- 19, AI- defined) or one that
            # carries on through a joining word (order- of-magnitude)
            broken = (
                line[:1].islower()
                and stem.isalpha()
                and not stem.isupper()
                and not (len(after) > 1 and after[0] in JOINERS)
            )
            out = (out[:-1] if broken else out) + line
        else:
            out = (out + " " + line).strip()
    return out
