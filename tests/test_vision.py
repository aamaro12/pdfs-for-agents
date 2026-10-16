"""OCR with Apple Vision, where it is available (macOS 26 or later with the command line tools)."""

import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfscope import vision
from pdfscope.document import Document
from pdfscope.render import page_markdown

REASON = vision.available()


class HelperBuildTests(unittest.TestCase):
    def test_a_cache_folder_that_cannot_be_written_means_no_ocr_not_a_crash(self):
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            os.chmod(locked, 0o555)
            try:
                with patch.object(vision, "CACHE", locked):
                    with self.assertRaises(vision.Unavailable):
                        vision._build()
            finally:
                os.chmod(locked, 0o755)


@unittest.skipIf(REASON, REASON or "")
class VisionTests(unittest.TestCase):
    def test_a_scanned_page_is_read_by_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_pdf = Path(tmp) / "text.pdf"
            c = canvas.Canvas(str(text_pdf))
            c.setFont("Helvetica", 18)
            c.drawString(72, 700, "Quarterly revenue rose to 1,320")
            c.save()
            with Document(str(text_pdf), ocr="never", cache=False) as doc:
                image = doc.pdf.render(1, 200)
            scan = Path(tmp) / "scan.pdf"
            image.save(scan, "PDF", resolution=200)  # an image only: no text layer
            with Document(str(scan), cache=False) as doc:
                page = doc.page(1)
            self.assertEqual(page.source, "ocr")
            self.assertIn("1,320", page_markdown(page))
            self.assertTrue(any("no text layer" in w for w in page.warnings))

    def test_the_helper_is_started_again_if_it_stops(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (800, 200), "white")
        ImageDraw.Draw(image).text((20, 80), "Revenue rose to 1,320", fill="black", font_size=40)
        with vision.Vision() as engine:
            engine._proc.kill()
            engine._proc.wait()
            found = engine.read_image(image)
        self.assertIn("1,320", " ".join(line["text"] for line in found["lines"]))


if __name__ == "__main__":
    unittest.main()
