"""Figures: drawn charts and images become blocks; the figure step reads them (models stubbed here)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from reportlab.lib.utils import ImageReader

from pdfscope import figures, reader, vision
from pdfscope.document import Document
from pdfscope.render import figure_markdown, page_markdown
from support import EXAMPLE, FakeOCR, ocr_lines, page_with_picture


class FigureTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document(str(EXAMPLE), ocr="never", cache=False)
        self.addCleanup(self.doc.close)

    def chart(self):
        page = self.doc.page(3)
        k, block = next((k, b) for k, b in enumerate(page.blocks, 1) if b.kind == "figure")
        return f"p3:b{k}", block

    def test_a_drawn_chart_is_a_figure_with_its_caption_and_labels(self):
        cite, block = self.chart()
        self.assertEqual(block.info["source"], "drawing")
        self.assertEqual(block.text, "Figure 1: Revenue by region, 2026 and 2025 (thousands of dollars)")
        for label in ("310", "196", "North", "Online", "2026"):
            self.assertIn(label, block.info["labels"])
        # the chart's numbers are not read as running text, nor as a table
        page = self.doc.page(3)
        self.assertNotIn("310", page_markdown(page))
        self.assertFalse(any(b.kind == "table" for b in page.blocks))
        self.assertIn("Figure 1", reader.outline(self.doc)["outline"][2]["figures"][0]["caption"])

    def test_a_ruled_table_is_not_a_chart(self):
        self.assertFalse(any(b.kind == "figure" for b in self.doc.page(2).blocks))

    def test_figure_step_checks_the_table_against_printed_labels(self):
        cite, _ = self.chart()
        table = [["", "2026", "2025"], ["North", "310", "280"], ["South", "285", "999"]]
        with (
            patch.object(figures, "describe", return_value="A bar graph of revenue by region."),
            patch.object(figures, "chart_table", return_value=table),
        ):
            f = reader.figure(self.doc, cite)
        self.assertTrue(f["chart"])
        self.assertEqual(f["table"], table)
        self.assertEqual(f["unconfirmed"], ["999"])
        self.assertEqual(f["labels_from"], "text layer")
        text = figure_markdown(f)
        self.assertIn("check these: 999", text)
        self.assertIn("the PDF's text, exact", text)

    def test_a_photo_is_described_but_not_tabulated(self):
        cite, _ = self.chart()
        with (
            patch.object(figures, "describe", return_value="A dog looking up at a light bulb."),
            patch.object(figures, "looks_like_chart", return_value=False),
            patch.object(figures, "chart_table", side_effect=AssertionError("not a chart")),
        ):
            f = reader.figure(self.doc, cite)
        self.assertEqual((f["chart"], f["table"]), (False, None))

    def test_only_figures_can_be_read_as_figures(self):
        with self.assertRaisesRegex(reader.ReadError, "is a heading, not a figure"):
            reader.figure(self.doc, "p1:b2")

    def test_axis_ticks_and_years_do_not_count_as_printed_figures(self):
        table = [["", "2026"], ["North", "310"], ["South", "285"]]
        ticks = ["0", "100", "200", "300", "400", "2026", "2025", "FY2024", "North", "South"]
        self.assertIsNone(reader._check_against_labels(table, ticks))
        table += [["East", "999"], ["West", "300"]]
        printed = ticks + ["310", "285", "240", "205"]
        self.assertEqual(reader._check_against_labels(table, printed), ["999", "300"])

    def test_missing_models_say_how_to_install_them(self):
        figures._models.clear()
        with (
            patch.dict(sys.modules, {"mlx_vlm": None}),
            patch.object(figures.platform, "system", return_value="Darwin"),  # as on a Mac, wherever the tests run
            patch.object(figures.platform, "machine", return_value="arm64"),
        ):
            with self.assertRaisesRegex(
                figures.Unavailable, r"figures extra.*git\+https://github\.com/aamaro12/pdfs-for-agents"
            ):
                figures.describe(Image.new("RGB", (40, 40)))

    def test_chart_detection_from_description_or_labels(self):
        self.assertTrue(figures.looks_like_chart("A bar graph with green bars.", []))
        self.assertTrue(figures.looks_like_chart("", ["$80", "$130", "$180", "12%", "40", "(4.1)"]))
        self.assertFalse(figures.looks_like_chart("A rabbit on a carpet.", ["Fig. 2"]))


class ImageFileTests(unittest.TestCase):
    def test_an_image_file_opens_as_a_one_page_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.png"
            Image.new("RGB", (400, 300), "white").save(path)
            with Document(str(path), ocr="never", cache=False) as doc:
                self.assertEqual(doc.pages, 1)
                self.assertTrue(doc.pdf.is_image)
                # without OCR the image is still a figure the figure step can describe
                blocks = doc.page(1).blocks
                self.assertEqual([(b.kind, b.info["source"]) for b in blocks], [("figure", "image file")])

    def test_each_frame_of_a_multi_frame_image_is_a_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.tif"
            frames = [Image.new("RGB", (300, 400), (255, 255, 255 - k)) for k in range(3)]
            frames[0].save(path, save_all=True, append_images=frames[1:])
            with Document(str(path), ocr="never", cache=False) as doc:
                self.assertEqual(doc.pages, 3)


class PictureTests(unittest.TestCase):
    """A picture with no text over it is read by OCR: its words are a figure's labels, or a table."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "picture.pdf"
        self.box = page_with_picture(self.path)

    def page(self, found):
        with Document(str(self.path), cache=False, engine=FakeOCR(found)) as doc:
            return doc.page(1)

    def test_text_read_inside_a_picture_is_marked_as_ocr(self):
        x0, y0 = self.box[:2]
        words = ("North", "310", "South", "285")
        found = ocr_lines(
            *[(t, (x0 + 0.05 + 0.1 * k, y0 + 0.05, x0 + 0.09 + 0.1 * k, y0 + 0.065)) for k, t in enumerate(words)]
        )
        page = self.page(found)
        shown = [b for b in page.blocks if b.kind == "figure"]
        self.assertEqual(len(shown), 1)
        self.assertEqual((shown[0].info["labels_from"], shown[0].info["labels"]), ("ocr", list(words)))
        # as with text drawn over a picture, it stays in the page too: it may be a table inside a figure
        self.assertIn("310", page_markdown(page))

    def test_text_drawn_over_a_picture_is_not_read_twice(self):
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(self.path), pagesize=(612, 792))
        c.setFont("Helvetica", 10)
        c.drawString(72, 720, "Revenue by region for the year, as shown in the picture below.")
        c.drawImage(ImageReader(Image.new("RGB", (300, 200), (200, 220, 240))), 72, 300, 300, 200)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(82, 480, "Revenue by region")  # a title over the picture, in the text layer
        c.save()

        def box(x, y, w, h):
            return (x / 612, (792 - y - h) / 792, (x + w) / 612, (792 - y) / 792)

        found = ocr_lines(
            ("Revenue", box(82, 478, 50, 13)),
            ("by", box(136, 478, 14, 13)),
            ("region", box(154, 478, 36, 13)),
            ("North", box(100, 400, 30, 10)),
            ("310", box(140, 400, 20, 10)),
        )
        text = page_markdown(self.page(found))
        self.assertEqual(text.count("Revenue"), 1)  # the title, not the title and OCR's reading of it
        self.assertIn("310", text)

    def test_pictures_left_unread_for_want_of_ocr_are_flagged_and_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pdfscope.vision.CACHE", Path(tmp)):
            with patch.object(vision, "Vision", side_effect=vision.Unavailable("Apple Vision OCR needs macOS")):
                with Document(str(self.path)) as doc:
                    page = doc.page(1)
                    self.assertTrue(any(w.startswith("OCR unavailable") for w in page.warnings))
                    self.assertFalse(doc._cache_file(1).exists())

    def test_a_legend_read_as_a_table_does_not_hide_the_chart(self):
        x0, y0, x1, y1 = self.box
        legend = [("North", 0), ("South", 1), ("Online", 2)]
        cells = [{"rows": [0, 0], "cols": [c, c], "text": t} for t, c in legend]
        lines = [(t, (x0 + 0.05 + 0.08 * c, y0 + 0.02, x0 + 0.1 + 0.08 * c, y0 + 0.035)) for t, c in legend]
        lines += [
            (t, (x0 + 0.05 + 0.1 * k, y1 - 0.04, x0 + 0.08 + 0.1 * k, y1 - 0.025)) for k, t in enumerate(("310", "285"))
        ]
        table = {"box": [x0 + 0.04, y0 + 0.015, x0 + 0.3, y0 + 0.04], "cells": cells}
        page = self.page(ocr_lines(*lines, table))
        self.assertEqual([b.kind for b in page.blocks if b.kind == "figure"], ["figure"])

    def test_a_table_pasted_as_a_picture_is_a_table_not_a_figure(self):
        x0, y0, x1, y1 = self.box
        rows = [["", "2026", "2025"], ["North", "310", "280"], ["South", "285", "262"]]
        cells = [{"rows": [r, r], "cols": [c, c], "text": t} for r, row in enumerate(rows) for c, t in enumerate(row)]
        lines = [
            (t, (x0 + 0.02 + 0.12 * c, y0 + 0.03 + 0.03 * r, x0 + 0.08 + 0.12 * c, y0 + 0.045 + 0.03 * r))
            for r, row in enumerate(rows)
            for c, t in enumerate(row)
            if t
        ]
        page = self.page(ocr_lines(*lines, {"box": [x0 + 0.01, y0 + 0.01, x1 - 0.01, y1 - 0.01], "cells": cells}))
        self.assertEqual([b.kind for b in page.blocks if b.kind in ("figure", "table")], ["table"])
        self.assertEqual(next(b.rows for b in page.blocks if b.kind == "table"), rows)


if __name__ == "__main__":
    unittest.main()
