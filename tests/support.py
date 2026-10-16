"""Shared test fixtures: the example report, and spans to feed the layout rules directly."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import make_northwind  # noqa: E402, F401 (re-exported for the tests)

from pdfscope.pdf import Span  # noqa: E402

EXAMPLE = ROOT / "examples" / "northwind.pdf"


def words(
    text: str, x0: float, y0: float, height: float = 0.012, char: float = 0.008, bold: bool = False
) -> list[Span]:
    """Words of a line laid out from x0, one char width per character and one space between words."""
    out, x = [], x0
    for word in text.split():
        out.append(Span(word, x, y0, x + char * len(word), y0 + height, bold))
        x += char * (len(word) + 1)
    return out


def phrase(text: str, x0: float, y0: float, **kw) -> Span:
    ws = words(text, x0, y0, **kw)
    return Span(text, ws[0].x0, ws[0].y0, ws[-1].x1, ws[-1].y1, ws[0].bold, -1, ws)


class FakeOCR:
    """Stands in for Apple Vision: returns the same reading for any image, or fails like it can."""

    def __init__(self, found=None, error=None):
        self.found, self.error = found, error

    def read_image(self, image):
        from pdfscope import vision

        if self.error:
            raise vision.Unavailable(self.error)
        return self.found

    def close(self):
        pass


def ocr_lines(*items):
    """An OCR reading: (text, box) lines, each line one word, and any tables as {"box", "cells"}."""
    lines = [{"text": t, "box": list(b)} for t, b in (i for i in items if not isinstance(i, dict))]
    tables = [i for i in items if isinstance(i, dict)]
    return {"lines": lines, "words": lines, "paragraphs": [], "tables": tables}


def page_with_picture(path) -> tuple[float, float, float, float]:
    """A page with a paragraph over a picture that has no text drawn on it; returns the picture's box."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=(612, 792))
    c.setFont("Helvetica", 10)
    c.drawString(72, 720, "Revenue by region for the year, as shown in the picture below.")
    c.drawImage(ImageReader(Image.new("RGB", (300, 200), (200, 220, 240))), 72, 300, 300, 200)
    c.save()
    return (72 / 612, (792 - 500) / 792, 372 / 612, (792 - 300) / 792)
