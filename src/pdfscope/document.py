"""A PDF read page by page: the text layer when it can be trusted, OCR when it cannot, then layout."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field

from importlib import resources

from . import __version__, vision
from .layout import Block, layout, margin_key, margin_rows, rows_of, table_from_cells
from .pdf import PDF, Span, phrases


def _code_key() -> str:
    """A hash of the code that shapes pages, so pages cached by an older version are read again."""
    digest = hashlib.sha256(__version__.encode())
    for name in ("pdf.py", "layout.py", "document.py", "vision.swift"):
        digest.update(resources.files("pdfscope").joinpath(name).read_bytes())
    return digest.hexdigest()[:10]


CODE_KEY = _code_key()


@dataclass
class Page:
    number: int  # 1-based, as a PDF viewer counts
    label: str | None  # the page label the PDF declares, if any
    source: str  # "text layer", "ocr" or "none"
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)
    margins: list[str] = field(default_factory=list)  # its margin texts, to spot running headers on nearby pages


def _ocr_missing(page: Page) -> bool:
    """Whether OCR was wanted on the page but did not run, so it is worth reading again later."""
    return any(w.startswith(("OCR unavailable", "OCR failed", "OCR of an image")) for w in page.warnings)


class Document:
    """An open PDF. `ocr` is "auto" (only where the text layer fails), "always" or "never"."""

    def __init__(
        self,
        path: str,
        ocr: str = "auto",
        languages: str = "en-US",
        dpi: int = 200,
        engine: vision.Vision | None = None,
        cache: bool = True,
    ):
        if ocr not in ("auto", "always", "never"):
            raise ValueError("ocr must be auto, always or never")
        self.path = path
        self.cache = cache and not os.environ.get("PDFSCOPE_NO_CACHE")
        self._sha256: str | None = None
        self.ocr = ocr
        self.languages = languages
        self.dpi = dpi
        self.pdf = PDF(path)
        self.pages = self.pdf.pages
        self._cache: dict[int, Page] = {}
        self._margins: dict[int, set[str]] = {}  # margin texts per page, to spot running headers
        self._found: dict[int, dict] = {}  # OCR of a scanned page read early for its margins, kept until it is read
        self._vision: vision.Vision | None = engine  # pass one in to share it across documents
        self._own_vision = engine is None
        self._vision_error: str | None = None

    def close(self) -> None:
        if self._vision and self._own_vision:
            self._vision.close()
        self.pdf.close()

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def sha256(self) -> str:
        if self._sha256 is None:
            digest = hashlib.sha256()
            with open(self.path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            self._sha256 = digest.hexdigest()
        return self._sha256

    def _cache_file(self, number: int):
        key = f"{CODE_KEY}-{self.ocr}-{self.languages}-{self.dpi}"
        return vision.CACHE / "pages" / self.sha256[:16] / key / f"p{number}.json"

    def page(self, number: int) -> Page:
        if not 1 <= number <= self.pages:
            raise IndexError(f"page {number} is outside 1 to {self.pages}")
        if number not in self._cache:
            page = self._load(number) if self.cache else None
            if page is None:
                page = self._read(number)
                if self.cache:
                    self._store(page)
            self._margins.setdefault(number, set(page.margins))
            if _ocr_missing(page):
                return page  # not kept, so the next call tries OCR again
            self._cache[number] = page
        return self._cache[number]

    def is_cached(self, number: int) -> bool:
        """Whether the page has been read already, in memory or on disk, so asking for it costs no OCR."""
        return number in self._cache or (self.cache and self._cache_file(number).exists())

    def _figure_file(self, number: int, index: int, box):
        from . import figures

        key = (
            f"{CODE_KEY}|{figures.DESCRIBER}|{figures.CHART_READER}|{figures.CHART_WIDTH}|{[round(v, 4) for v in box]}"
        )
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return vision.CACHE / "figures" / self.sha256[:16] / f"p{number}-b{index}-{digest}.json"

    def cached_figure(self, number: int, index: int, box) -> dict | None:
        if not self.cache:
            return None
        try:
            return json.loads(self._figure_file(number, index, box).read_text())
        except (OSError, ValueError):
            return None

    def store_figure(self, number: int, index: int, box, result: dict) -> None:
        if not self.cache:
            return
        path = self._figure_file(number, index, box)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result))
        except OSError:
            pass

    def _load(self, number: int) -> Page | None:
        try:
            data = json.loads(self._cache_file(number).read_text())
        except (OSError, ValueError):
            return None
        blocks = [
            Block(b["kind"], tuple(b["box"]), b["text"], b["level"], b["rows"], info=b.get("info"))
            for b in data["blocks"]
        ]
        return Page(data["number"], data["label"], data["source"], blocks, data["warnings"], data.get("margins", []))

    def _store(self, page: Page) -> None:
        if _ocr_missing(page):
            return  # OCR was wanted but did not run: read again next time in case it works then
        data = asdict(page)
        for b in data["blocks"]:
            b.pop("spans", None)
        path = self._cache_file(page.number)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass

    def _ocr(self) -> vision.Vision | None:
        if self._vision is None and self._vision_error is None:
            try:
                self._vision = vision.Vision(self.languages)
            except vision.Unavailable as e:
                self._vision_error = str(e)
        return self._vision

    def _margin_texts(self, number: int, spans=None) -> set[str] | None:
        """The texts in a page's margins. For a scanned page not read yet, its OCR is done now and kept for when
        it is read, so a page's running headers do not depend on which pages happened to be read first."""
        if number in self._margins:
            return self._margins[number]
        if spans is None:
            page = self._cache.get(number) or (self._load(number) if self.cache else None)
            if page is not None:  # read before: its margins were kept with it
                self._margins[number] = set(page.margins)
                return self._margins[number]
            layer = self.pdf.text(number)
            if layer.state == "good" and self.ocr != "always":
                spans = layer.spans
            elif self.ocr != "never" and (engine := self._ocr()):
                try:
                    if number not in self._found:
                        self._found[number] = engine.read_image(self.pdf.render(number, self.dpi))
                except vision.Unavailable:
                    return None
                w, h = self.pdf.size(number)
                spans = ocr_spans(self._found[number], h / w)
            else:
                return None
        self._margins[number] = {margin_key(" ".join(s.text for s in r)) for r in margin_rows(rows_of(spans))}
        return self._margins[number]

    def _repeated(self, number: int) -> set[str] | None:
        """Margin texts that also appear on a nearby page, or None when there is no nearby page to compare."""
        seen, compared = set(), False
        for m in (number - 2, number - 1, number + 1, number + 2):
            if 1 <= m <= self.pages:
                texts = self._margin_texts(m)
                if texts is not None:
                    seen |= texts
                    compared = True
        return seen if compared else None

    def _read(self, number: int) -> Page:
        layer = self.pdf.text(number)
        warnings = []
        want_ocr = self.ocr == "always" or (self.ocr == "auto" and layer.state != "good")
        if want_ocr:
            engine = self._ocr()
            if engine:
                try:
                    found = self._found.pop(number, None)
                    if found is None:
                        found = engine.read_image(self.pdf.render(number, self.dpi))
                    w, h = self.pdf.size(number)
                    spans = ocr_spans(found, h / w)
                    self._margin_texts(number, spans)
                    if layer.state == "broken":
                        warnings.append(
                            "the text layer maps glyphs to the wrong characters, so the page was read by OCR"
                        )
                    elif layer.state == "empty":
                        warnings.append("the page has no text layer, so it was read by OCR")
                    if self.pdf.is_image:
                        # a picture, chart or diagram is a figure; a photographed page of text is just a page
                        words = sum(len(sp.text.split()) for sp in spans)
                        whole = {"box": (0.0, 0.0, 1.0, 1.0), "source": "image file", "labels_from": "ocr"}
                        figures = [whole] if words <= 80 else []
                    else:  # text OCR reads inside an embedded image (a chart's labels) belongs to that image,
                        # but an image holding most of the page's text is the scan itself, not a figure
                        figures = [
                            dict(f, absorb=True, labels_from="ocr")
                            for f in self._figures(number)
                            if not (f["source"] == "image" and _holds_most(f["box"], spans))
                        ]
                    blocks = layout(spans, found["tables"], self._repeated(number), h / w, figures=figures)
                    margins = sorted(self._margins.get(number, ()))
                    return Page(number, self.pdf.labels[number - 1], "ocr", blocks, warnings, margins)
                except vision.Unavailable as e:
                    warnings.append(f"OCR failed: {e}")
            else:
                warnings.append(f"OCR unavailable: {self._vision_error}")
        if layer.state == "broken":
            warnings.append("the text layer maps glyphs to the wrong characters; its text is unreliable")
        source = "text layer" if layer.spans else "none"
        spans, tables, bare = layer.spans, None, []
        if layer.state == "good":
            self._margin_texts(number, spans)
            spans, tables, bare = self._read_images(number, spans, warnings)
        figures = []
        for f in self._figures(number):
            if f["source"] == "image" and any(_same_box(f["box"], b) for b in bare):
                if any(_fills(t, f["box"]) for t in tables or []):
                    continue  # a table pasted as a picture: it is read as a table, not a figure
                # its text came from OCR; it stays in the page too, as text drawn over a picture does
                f = dict(f, labels_from="ocr")
            figures.append(f)
        if self.pdf.is_image:  # without OCR an image file is still a figure the figure step can describe
            figures.append({"box": (0.0, 0.0, 1.0, 1.0), "source": "image file", "labels_from": "ocr"})
        rules = self.pdf.rules(number)
        blocks = layout(spans, tables, self._repeated(number), layer.aspect, rules, figures)
        margins = sorted(self._margins.get(number, ()))
        return Page(number, self.pdf.labels[number - 1], source, blocks, warnings, margins)

    def _figures(self, number: int) -> list[dict]:
        """Embedded images (not page backgrounds or icons) and drawn charts on a page."""
        out = [
            {"box": b, "source": "image"}
            for b in self.pdf.images(number, min_area=0.015)
            if (b[2] - b[0]) * (b[3] - b[1]) < 0.85
        ]
        out += [{"box": b, "source": "drawing"} for b in self.pdf.drawings(number)]
        return out

    def _read_images(self, number: int, spans: list[Span], warnings: list[str]):
        """OCR large images that have no text drawn over them (a table pasted as a picture), and add what they say."""
        if self.ocr == "never":
            return spans, None, []
        bare = []
        for x0, y0, x1, y1 in self.pdf.images(number):
            over = sum(1 for s in spans if x0 <= (s.x0 + s.x1) / 2 <= x1 and y0 <= (s.y0 + s.y1) / 2 <= y1)
            if over < 3:
                bare.append((x0, y0, x1, y1))
        if not bare:
            return spans, None, []
        if not (engine := self._ocr()):
            warnings.append(f"OCR unavailable: {self._vision_error}; text in pictures on the page was not read")
            return spans, None, []
        try:
            found = engine.read_image(self.pdf.render(number, self.dpi))
        except vision.Unavailable as e:
            warnings.append(f"OCR of an image on the page failed: {e}")
            return spans, None, []

        def within(box):
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            return any(a <= cx <= c and b <= cy <= d for a, b, c, d in bare)

        w, h = self.pdf.size(number)
        # what OCR reads over a picture that the text layer already has (a title drawn on it) is left out
        extra = [s for s in ocr_spans(found, h / w) if within(s.box) and not _covered(s.box, spans)]
        if extra:
            warnings.append(f"text in {len(bare)} image(s) on the page was read by OCR")
        return spans + extra, [t for t in found["tables"] if within(t["box"])], bare


def _same_box(a, b, tol: float = 0.01) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _covered(box, spans) -> bool:
    area = (box[2] - box[0]) * (box[3] - box[1])
    return area > 0 and any(_overlap(box, s.box) >= 0.5 * area for s in spans)


def _fills(table: dict, box) -> bool:
    """Whether an OCR table is the picture: rows and columns covering much of it, not a legend inside a chart."""
    rows = table_from_cells(table["cells"])
    area = (box[2] - box[0]) * (box[3] - box[1])
    return len(rows) >= 2 and max(map(len, rows)) >= 2 and _overlap(table["box"], box) >= 0.4 * area


def _holds_most(box, spans) -> bool:
    """Whether most of a page's text lies inside a box: a scanned page drawn as one image."""
    inside = sum(1 for s in spans if _centre_in(s.box, box))
    return len(spans) >= 5 and inside >= 0.5 * len(spans)


def _centre_in(inner, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def ocr_spans(found: dict, aspect: float) -> list[Span]:
    """Spans from OCR: Vision's own lines, split at wide gaps between their words (table columns)."""
    paragraphs = [p["box"] for p in found["paragraphs"]]
    words = [(w["text"], w["box"]) for w in found.get("words") or []]
    spans = []
    for line in found.get("textLines") or found["lines"]:
        lx0, ly0, lx1, ly1 = line["box"]
        cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
        group = next((k for k, (a, b, c, d) in enumerate(paragraphs) if a <= cx <= c and b <= cy <= d), -1)
        inside = sorted(
            (
                w
                for w in words
                if lx0 - 0.002 <= (w[1][0] + w[1][2]) / 2 <= lx1 + 0.002 and ly0 <= (w[1][1] + w[1][3]) / 2 <= ly1
            ),
            key=lambda w: w[1][0],
        )
        if len("".join(t for t, _ in inside)) < 0.8 * len(line["text"].replace(" ", "")):
            spans.append(Span(line["text"], lx0, ly0, lx1, ly1, group=group))
            continue
        words_here = [Span(t, b[0], ly0, b[2], ly1, group=group) for t, b in inside]
        spans += phrases(words_here, aspect=aspect)
    return spans
