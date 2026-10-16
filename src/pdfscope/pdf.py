"""The PDF itself, through pdfium: page sizes, rendering, and the text layer as spans with boxes.

Boxes everywhere in pdfscope are fractions of the page, with the origin at the top left, so a span
read from the text layer and a line read by OCR can be compared directly.
"""

from __future__ import annotations

import ctypes
import io
import re
import statistics
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as raw

# pdfium is not thread-safe: every call into it goes through this lock.
LOCK = threading.RLock()

BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.I)


@dataclass
class Span:
    """A run of text on one line, with no wide gap inside it."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    bold: bool = False
    group: int = -1  # the OCR paragraph the span belongs to, when OCR found paragraphs
    words: list | None = None  # the words inside the span, each a Span, when known

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class TextLayer:
    words: list[Span]
    chars: int  # printable characters
    broken: int  # characters with no usable Unicode mapping, control characters or U+FFFD
    aspect: float = 1.29  # page height over width, to compare horizontal gaps with line heights

    @property
    def state(self) -> str:
        """'good', 'empty' (nothing usable to read) or 'broken' (a font maps its glyphs to the wrong characters)."""
        if self.chars < 20:
            return "empty"
        if self.broken > 0.05 * self.chars:
            return "broken"
        if not plausible(" ".join(s.text for s in self.words)):
            return "broken"
        return "good"

    @property
    def spans(self) -> list[Span]:
        return phrases(self.words, aspect=self.aspect)


def phrases(words: list[Span], gap: float = 1.0, aspect: float = 1.29) -> list[Span]:
    """Join words that follow each other on a line into spans, breaking at gaps wider than `gap` line heights.

    Boxes are page fractions, so a horizontal gap is scaled by the page's aspect (height over width).
    """
    out: list[Span] = []
    cur: list[Span] = []

    def flush():
        if cur:
            bold = sum(w.bold for w in cur) * 2 > len(cur)
            out.append(
                Span(
                    " ".join(w.text for w in cur),
                    min(w.x0 for w in cur),
                    min(w.y0 for w in cur),
                    max(w.x1 for w in cur),
                    max(w.y1 for w in cur),
                    bold,
                    cur[0].group,
                    list(cur),
                )
            )
            cur.clear()

    for w in words:
        if cur:
            last = cur[-1]
            h = max(last.height, w.height)
            same_line = abs((w.y0 + w.y1) / 2 - (last.y0 + last.y1) / 2) < 0.5 * h
            if not same_line or w.x0 - last.x1 > gap * h * aspect or w.x0 < last.x0:
                flush()
        cur.append(w)
    flush()
    return out


# common words of the main Latin-script languages, to tell running text from a shuffled font map
COMMON = set(
    """the and of to in a is that for on with as by are this from or be at an was it its we our not
de la le les des et en du un une est que il pour dans par sur au qui el los las del y por con para una es
der die das und den von zu mit ist im dem nicht ein eine auf für di che per non il gli della e o da do em
het van een te dat op voor niet""".split()
)

VOWELS = set("aeiouyAEIOUYàáâäèéêëìíîïòóôöùúûüÀÁÂÄÈÉÊËÌÍÎÏÒÓÔÖÙÚÛÜ")


def plausible(text: str) -> bool:
    """Whether Latin-script text reads like words rather than a shuffled font map.

    Text in other scripts, or with too few words to judge, passes.
    """
    tokens = text.split()
    odd = sum(1 for t in tokens if re.search(r"[^\W\d_][?<>@=\[\]\\^{}|~;][^\W\d_]", t))
    if len(tokens) >= 15 and odd > 0.2 * len(tokens):
        return False  # letters and symbols interleaved, as when a font maps glyphs to the wrong characters
    alpha = [t.strip(".,;:()\"'").lower() for t in tokens if re.fullmatch(r"[^\W\d_]{2,}[.,;:]?", t)]
    figures = sum(1 for t in tokens if re.search(r"\d", t))
    if len(alpha) >= 20 and figures < 0.3 * len(tokens):
        common = sum(1 for t in alpha if t in COMMON) / len(alpha)
        capitals = sum(1 for t in tokens if len(t) >= 3 and t.isalpha() and t.isupper()) / len(alpha)
        if common < 0.02 and capitals > 0.5:
            return False  # no common words at all, and mostly capitals: letters shifted by a broken font map
    words = re.findall(r"[^\W\d_]{3,}", text)
    latin = [w for w in words if all(c.isascii() or c in VOWELS for c in w)]
    if len(latin) < 15:
        return True
    with_vowel = sum(1 for w in latin if any(c in VOWELS for c in w))
    return with_vowel / len(latin) >= 0.8


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".heif")


def image_as_pdf(path: str) -> bytes:
    """An image file as a one-page PDF with no text layer, so it is read like a scanned page."""
    from PIL import Image, ImageOps, ImageSequence

    if path.lower().endswith((".heic", ".heif")):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError:
            raise ValueError("reading HEIC images needs pillow-heif: pip install pillow-heif") from None

    def upright(frame):
        frame = ImageOps.exif_transpose(frame)  # a photo taken with the phone turned is shown turned back
        if frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info):
            frame = frame.convert("RGBA")
            return Image.alpha_composite(Image.new("RGBA", frame.size, "white"), frame).convert("RGB")
        return frame.convert("RGB")

    with Image.open(path) as im:
        frames = [upright(f) for f in ImageSequence.Iterator(im)]
    buf = io.BytesIO()
    frames[0].save(buf, "PDF", resolution=150, save_all=True, append_images=frames[1:])
    return buf.getvalue()


class _Shown:
    """Maps page space (points from the bottom left, before /Rotate) to the page as a viewer shows it: inside
    the crop box, turned by /Rotate, in points from the top left. render() draws the page the same way."""

    def __init__(self, page):
        self.rotation = page.get_rotation() % 360
        crop, media = page.get_cropbox(), page.get_mediabox()
        # the page is where the crop box and media box overlap, as pdfium sizes and renders it
        self.left, self.bottom = max(crop[0], media[0]), max(crop[1], media[1])
        self.right, self.top = min(crop[2], media[2]), min(crop[3], media[3])
        if self.right <= self.left or self.top <= self.bottom:
            self.left, self.bottom, self.right, self.top = media
        self.width, self.height = page.get_size()

    def point(self, x: float, y: float) -> tuple[float, float]:
        u, v = x - self.left, self.top - y
        across, down = self.right - self.left, self.top - self.bottom
        if self.rotation == 90:
            return down - v, u
        if self.rotation == 180:
            return across - u, down - v
        if self.rotation == 270:
            return v, across - u
        return u, v

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
        """A box in page space as (left, top, right, bottom) in points on the shown page."""
        (a, b), (c, d) = self.point(x0, y0), self.point(x1, y1)
        return min(a, c), min(b, d), max(a, c), max(b, d)

    def fraction(self, box) -> tuple[float, float, float, float]:
        left, top, right, bottom = box
        return left / self.width, top / self.height, right / self.width, bottom / self.height


class PDF:
    """An open PDF, or an image file read as a one-page PDF. Use it as a context manager, or call close()."""

    def __init__(self, path: str):
        self.path = path
        self.is_image = Path(path).suffix.lower() in IMAGE_SUFFIXES
        with LOCK:
            self._doc = pdfium.PdfDocument(image_as_pdf(path) if self.is_image else path)
            self.pages = len(self._doc)
            self.labels = [self._label(i) for i in range(self.pages)]

    def __enter__(self) -> PDF:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with LOCK:
            self._doc.close()

    def _label(self, index: int) -> str | None:
        n = raw.FPDF_GetPageLabel(self._doc.raw, index, None, 0)
        if n <= 2:
            return None
        buf = ctypes.create_string_buffer(n)
        raw.FPDF_GetPageLabel(self._doc.raw, index, buf, n)
        return buf.raw[: n - 2].decode("utf-16-le", errors="replace") or None

    @contextmanager
    def _page(self, number: int):
        """A page, opened and closed while holding the lock: pdfium must not close a page while another
        thread is inside it."""
        with LOCK:
            page = self._doc[number - 1]
            try:
                yield page
            finally:
                page.close()

    def size(self, number: int) -> tuple[float, float]:
        """Width and height of a page as shown (inside its crop box, turned by /Rotate), in points."""
        with self._page(number) as page:
            return tuple(page.get_size())

    def render(self, number: int, dpi: int = 200):
        """The page as a PIL image."""
        with self._page(number) as page:
            bitmap = page.render(scale=dpi / 72)
            try:
                return bitmap.to_pil().copy()  # a copy that does not share the bitmap's memory
            finally:
                bitmap.close()

    def _image_boxes(self, page) -> list[tuple[float, float, float, float]]:
        shown = _Shown(page)
        out = []
        for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_IMAGE,), max_depth=2):
            x0, y0, x1, y1 = shown.fraction(shown.box(*obj.get_bounds()))
            out.append((max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)))
        return out

    def image_cover(self, number: int) -> float:
        """The largest fraction of the page covered by a single image, a hint that the page is a scan."""
        with self._page(number) as page:
            boxes = self._image_boxes(page)
        return max((max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) for b in boxes), default=0.0)

    def images(self, number: int, min_area: float = 0.08) -> list[tuple[float, float, float, float]]:
        """Boxes of images covering at least min_area of the page, as fractions with the origin at the top left."""
        with self._page(number) as page:
            boxes = self._image_boxes(page)
        return [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) >= min_area]

    def drawings(self, number: int) -> list[tuple[float, float, float, float]]:
        """Regions of vector drawing that look like charts: clusters of bars, plotted lines and markers.

        Thin rules and wide shading (how tables are drawn) do not count, so a ruled table is not a drawing.
        Boxes are page fractions with the origin at the top left.
        """
        with self._page(number) as page:
            shown = _Shown(page)
            w, h = shown.width, shown.height
            fill, stroke = ctypes.c_int(), ctypes.c_int()
            shapes, strokes = [], []
            for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_PATH,), max_depth=3):
                left, top, right, bottom = shown.box(*obj.get_bounds())
                bw, bh = right - left, bottom - top
                box = list(shown.fraction((left, top, right, bottom)))
                if bw * bh > 0.8 * w * h:
                    continue  # page backgrounds
                if box[0] < -0.01 or box[1] < -0.01 or box[2] > 1.01 or box[3] > 1.01:
                    continue  # printer's marks and bleed run off the page; a chart does not
                if (bh <= 2.5 and bw >= 0.04 * w) or (bw <= 2.5 and bh >= 0.01 * h) or (bw <= 6 and bh <= 6):
                    strokes.append(box)  # ruling lines, axes and ticks: part of a chart only if they touch one
                    continue
                segments = raw.FPDFPath_CountSegments(obj.raw)
                raw.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke))
                bar = fill.value and segments <= 6 and bw < 0.3 * w and bh > 2.5
                # a plotted line runs across the chart; a filled shape with slanted or curved edges is a pie slice
                # or an area only when it is tall too. Letters drawn as outlines are small shapes of the same kind
                line = (
                    segments > 2
                    and bw > 0.05 * w
                    and ((stroke.value and not fill.value) or bh > 0.03 * h)
                    and _plotted(obj, segments)
                )
                marker = segments > 3 and bw < 0.02 * w and bh < 0.02 * h
                if bar or line or marker:
                    shapes.append(box + ["bar" if bar else "line" if line else "marker"])
        regions = []
        # bars of one chart can stand further apart than the gap that joins shapes, so each run of evenly
        # spaced bars on a shared baseline is joined first
        runs = _bar_runs([b for b in shapes if b[4] == "bar"])
        for r in _clusters([b[:4] for b in shapes] + runs, gap=0.03, min_items=3, min_area=0.015):
            inside = [b for b in shapes if r[0] <= b[0] and b[2] <= r[2] and r[1] <= b[1] and b[3] <= r[3]]
            bars = [b for b in inside if b[4] == "bar"]
            lines = [b for b in inside if b[4] == "line"]
            # a chart has plotted lines, or bars that vary in length; table shading is all one size,
            # and icons or check marks alone are not a chart
            if not lines and not _bar_chart(bars):
                continue
            r = list(r)
            for b in strokes:  # take in the axes and ticks that touch the chart
                if b[0] <= r[2] + 0.01 and r[0] - 0.01 <= b[2] and b[1] <= r[3] + 0.01 and r[1] - 0.01 <= b[3]:
                    if (b[2] - b[0]) <= 1.2 * (r[2] - r[0]) + 0.02:
                        r = [min(r[0], b[0]), min(r[1], b[1]), max(r[2], b[2]), max(r[3], b[3])]
            regions.append(tuple(r))
        return regions

    def rules(self, number: int) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
        """Ruling lines drawn on the page: horizontal ones as (y, x0, x1), vertical ones as (x, y0, y1), as fractions."""
        with self._page(number) as page:
            shown = _Shown(page)
            w, h = shown.width, shown.height
            horizontal, vertical = [], []
            for obj in page.get_objects(filter=(raw.FPDF_PAGEOBJ_PATH,), max_depth=3):
                x0, y0, x1, y1 = shown.box(*obj.get_bounds())
                if y1 - y0 <= 2.5 and x1 - x0 >= 0.04 * w:
                    horizontal.append(((y0 + y1) / 2 / h, x0 / w, x1 / w))
                elif x1 - x0 <= 2.5 and y1 - y0 >= 0.01 * h:
                    vertical.append(((x0 + x1) / 2 / w, y0 / h, y1 / h))
            return horizontal, vertical

    def text(self, number: int) -> TextLayer:
        """The page's text layer, as spans split at line breaks and wide gaps."""
        with self._page(number) as page:
            tp = page.get_textpage()
            try:
                return _spans(tp, _Shown(page))
            finally:
                tp.close()


def _spans(tp, shown: _Shown) -> TextLayer:
    """Words with boxes on the shown page. Characters outside the crop box are not on the page."""
    w, h = shown.width, shown.height
    n = tp.count_chars()
    rect = raw.FS_RECTF()
    font = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    spans: list[Span] = []
    cur: list = []  # [text parts, left, top, right, bottom, bold votes, char height], in points, y down
    printable = broken = 0

    def flush():
        nonlocal cur
        text = re.sub(r"\s+", " ", "".join(cur[0])).strip() if cur else ""
        # pdfium gives UTF-16 code units: join surrogate pairs, replace any left alone
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        if text:
            spans.append(Span(text, cur[1] / w, cur[2] / h, cur[3] / w, cur[4] / h, cur[5] > 0))
        cur = []

    for i in range(n):
        code = raw.FPDFText_GetUnicode(tp.raw, i)
        ch = chr(code) if 0 < code < 0x110000 else "�"
        if ch == "\x02":  # pdfium's mark for a word hyphenated at the end of a line
            if cur:
                cur[0].append("-")
            flush()
            continue
        if ch in "\r\n":
            flush()
            continue
        if ch.isspace():
            flush()
            continue
        raw.FPDFText_GetLooseCharBox(tp.raw, i, rect)
        left, top, right, bottom = shown.box(rect.left, rect.bottom, rect.right, rect.top)
        if right <= left or bottom <= top:
            continue
        if not (0 <= (left + right) / 2 <= w and 0 <= (top + bottom) / 2 <= h):
            continue  # outside the crop box: not on the page as shown
        printable += 1
        if raw.FPDFText_HasUnicodeMapError(tp.raw, i) == 1 or ch == "�" or ord(ch) < 32:
            broken += 1
        ch_h = bottom - top
        raw.FPDFText_GetFontInfo(tp.raw, i, font, len(font), ctypes.byref(flags))
        bold = 1 if BOLD.search(font.value.decode("latin-1")) else -1
        if cur:
            gap = left - cur[3]
            new_line = abs(bottom - cur[4]) > 0.5 * max(ch_h, cur[6]) and abs(top - cur[2]) > 0.5 * max(ch_h, cur[6])
            if new_line or gap > 0.12 * max(ch_h, cur[6]) or gap < -2 * ch_h:
                flush()
        if not cur:
            cur = [[ch], left, top, right, bottom, bold, ch_h]
        else:
            cur[0].append(ch)
            cur[1], cur[2] = min(cur[1], left), min(cur[2], top)
            cur[3], cur[4] = max(cur[3], right), max(cur[4], bottom)
            cur[5] += bold
            cur[6] = max(cur[6], ch_h)
    flush()
    return TextLayer(spans, printable, broken, h / w)  # spans here are single words


def _bar_chart(bars: list) -> bool:
    """Whether rectangles look like a bar chart: most share a baseline (bottom edge, or left edge for
    horizontal bars) and their lengths vary. Table cells and shading sit in several columns instead."""
    if len(bars) < 3:
        return False

    def shared(values):
        values = list(values)
        counts: dict[float, int] = {}
        for v in values:
            counts[round(v, 3)] = counts.get(round(v, 3), 0) + 1
        return max(counts.values()) >= 0.6 * len(values)

    vertical = shared(b[3] for b in bars) and len({round(b[3] - b[1], 3) for b in bars}) >= 3
    horizontal = shared(b[0] for b in bars) and len({round(b[2] - b[0], 3) for b in bars}) >= 3
    return vertical or horizontal


def _plotted(obj, segments: int) -> bool:
    """Whether a path has diagonal or curved segments, as a plotted line or a pie slice does.

    Table borders and boxes only move straight across or straight down, and a box with rounded corners
    (a card around a figure) adds no more than a curve at each corner.
    """
    x, y = ctypes.c_float(), ctypes.c_float()
    last, curves, straight = None, 0, 0
    for k in range(min(segments, 400)):
        seg = raw.FPDFPath_GetPathSegment(obj.raw, k)
        kind = raw.FPDFPathSegment_GetType(seg)
        raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        if kind == raw.FPDF_SEGMENT_BEZIERTO:
            curves += 1  # pdfium gives each cubic curve as 3 points
        elif last is not None and kind == raw.FPDF_SEGMENT_LINETO:
            across, down = abs(x.value - last[0]), abs(y.value - last[1])
            if across > 0.5 and down > 0.5:
                return True
            straight += across > 0.5 or down > 0.5
        last = (x.value, y.value)
    return curves > 0 and not (straight >= 2 and curves <= 4 * 3)


def _bar_runs(bars: list) -> list[list[float]]:
    """Boxes spanning each run of bars that share a baseline and stand evenly apart, as one chart's bars do.

    Bars are joined to the next while the gap is no wider than 1.6 times the run's usual gap, so two charts
    side by side, further apart than their bars, stay two.
    """
    runs = []
    for base, start, end, along in ((3, 0, 2, 0), (0, 1, 3, 1)):  # vertical bars on a bottom edge, then horizontal
        groups: dict[float, list] = {}
        for b in bars:
            groups.setdefault(round(b[base], 3), []).append(b)
        for group in groups.values():
            if len(group) < 3:
                continue
            group.sort(key=lambda b: b[start])
            gaps = [b[start] - a[end] for a, b in zip(group, group[1:])]
            usual = statistics.median([g for g in gaps if g > 0] or [0])
            run = [group[0]]
            for b, g in zip(group[1:], gaps):
                if g <= max(0.03, 1.6 * usual):
                    run.append(b)
                    continue
                if len(run) >= 3 and _bar_chart(run):
                    runs.append(_span_of(run))
                run = [b]
            if len(run) >= 3 and _bar_chart(run):  # lengths that vary, unlike shaded table cells
                runs.append(_span_of(run))
    return runs


def _span_of(boxes) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _clusters(boxes: list[list[float]], gap: float, min_items: int, min_area: float):
    """Boxes merged into regions wherever they lie within `gap` of each other (repeated until stable)."""
    groups = [(list(b), 1) for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[tuple[list[float], int]] = []
        for g, n in groups:
            for k, (o, m) in enumerate(out):
                if o[0] - gap <= g[2] and g[0] - gap <= o[2] and o[1] - gap <= g[3] and g[1] - gap <= o[3]:
                    out[k] = ([min(o[0], g[0]), min(o[1], g[1]), max(o[2], g[2]), max(o[3], g[3])], m + n)
                    changed = True
                    break
            else:
                out.append((g, n))
        groups = out
    return [tuple(g) for g, n in groups if n >= min_items and min_area <= (g[2] - g[0]) * (g[3] - g[1]) <= 0.9]
