"""The reading steps on the example report."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from pdfscope import document, reader
from pdfscope.document import Document
from pdfscope.render import find_markdown, outline_markdown, page_markdown, read_markdown
from support import EXAMPLE, FakeOCR, ocr_lines


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document(str(EXAMPLE), ocr="never", cache=False)
        self.addCleanup(self.doc.close)

    def table_cite(self):
        return next(f"p2:b{k}" for k, b in enumerate(self.doc.page(2).blocks, 1) if b.kind == "table")

    def test_outline_lists_pages_headings_and_tables(self):
        o = reader.outline(self.doc)
        self.assertEqual(o["pages"], 3)
        self.assertEqual(o["sources"], {"text layer": 3})
        self.assertEqual(o["outline"][0]["headings"][0][1], "A year of steady growth")
        self.assertEqual(o["outline"][1]["tables"][0]["first_row"], ["Dec 31, 2026", "Dec 31, 2025"])
        self.assertIn("Cite blocks like [northwind.pdf] p1:b2.", outline_markdown(o))

    def test_running_header_and_page_numbers_are_marked(self):
        kinds = {b.text: b.kind for b in self.doc.page(1).blocks}
        self.assertEqual(kinds["Northwind Trading · Annual Report 2026"], "header")
        self.assertEqual(kinds["1"], "footer")
        self.assertNotIn("Annual Report 2026", page_markdown(self.doc.page(1)))

    def test_columns_read_in_order(self):
        text = page_markdown(self.doc.page(1))
        self.assertLess(text.index("freight costs in the second half"), text.index("Operating costs rose"))

    def test_find_figures_by_value_and_scale(self):
        hits = reader.find(self.doc, "1.3m")["hits"]
        self.assertEqual([(h["text"], h["row"], h["column"]) for h in hits], [("1,320", "Revenue", "Dec 31, 2026")])
        self.assertEqual(reader.find(self.doc, "(12)")["hits"][0]["row"], "Interest expense")
        self.assertEqual(reader.find(self.doc, "freight")["hits"][0]["cite"], "p1:b3")
        self.assertEqual(reader.find(self.doc, "no such words")["hits"], [])

    def test_read_tags_blocks_with_citations(self):
        text = read_markdown(reader.read(self.doc, "2"))
        self.assertIn(f"[{self.table_cite()}] table", text)
        self.assertIn("| Net income | 167 | 139 |", text)

    def test_table_parses_figures_and_units(self):
        t = reader.table(self.doc, self.table_cite())
        self.assertEqual(t["units"], "thousands")
        row = next(r for r, v in zip(t["rows"], t["values"]) if r[0] == "Interest expense")
        self.assertEqual(t["values"][t["rows"].index(row)], [None, -12.0, -18.0])

    def test_show_crops_a_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.png"
            s = reader.show(self.doc, self.table_cite(), str(out), dpi=72)
            self.assertTrue(out.exists())
            self.assertLess(s["width"], 612)

    def test_find_matches_text_inside_figures(self):
        chart = next(f"p3:b{k}" for k, b in enumerate(self.doc.page(3).blocks, 1) if b.kind == "figure")
        hits = reader.find(self.doc, "310")["hits"]
        self.assertEqual([(h["cite"], h["kind"], h["text"]) for h in hits], [(chart, "figure label", "310")])
        self.assertEqual(reader.find(self.doc, "Online")["hits"][0]["kind"], "figure label")

    def test_years_above_figures_are_column_headings(self):
        rows = [["", "2024", "2023"], ["Revenue", "1,320", "1,200"], ["Net income", "167", "139"]]
        self.assertEqual(reader._column_headings(rows), ["", "2024", "2023"])
        rows = [["", "Q1", "Q2"], ["", "2024", "2024"], ["Revenue", "320", "330"]]
        self.assertEqual(reader._column_headings(rows), ["", "Q1 · 2024", "Q2 · 2024"])

    def test_a_time_budget_reads_a_long_document_over_several_calls(self):
        doc = Document(str(EXAMPLE), ocr="never", cache=False)
        self.addCleanup(doc.close)
        first = reader.outline(doc, budget=0)  # at least one page is read per call
        self.assertEqual(([p["page"] for p in first["outline"]], first["unread"]), ([1], [2, 3]))
        self.assertIn("Pages 2-3 not read yet", outline_markdown(first))
        second = reader.outline(doc, budget=0)
        self.assertEqual(([p["page"] for p in second["outline"]], second["unread"]), ([1, 2], [3]))
        self.assertEqual(reader.outline(doc, budget=0)["unread"], [])
        fresh = Document(str(EXAMPLE), ocr="never", cache=False)
        self.addCleanup(fresh.close)
        found = reader.find(fresh, "167", budget=0)
        self.assertEqual((found["hits"], found["unread"]), ([], [2, 3]))
        self.assertIn("call find again", find_markdown(found))
        self.assertEqual(reader.find(fresh, "167")["hits"][0]["row"], "Net income")

    def test_spaced_figures_are_one_number_only_in_groups_of_thousands(self):
        self.assertIsNone(reader.cell_value("2137 32.3"))  # two figures in one cell, not 213,732.3
        self.assertEqual(reader.cell_value("1 234"), 1234.0)
        self.assertEqual(reader.cell_value("12 345.6"), 12345.6)
        self.assertEqual(reader.cell_value("1 234 567"), 1234567.0)

    def test_find_reads_figures_in_prose_and_matches_either_sign(self):
        def hit(text, query, cell=False):
            return reader._matches(text, query, reader.number_query(query), cell=cell)

        self.assertTrue(hit("scores fell from 2019-2023", "2023"))
        self.assertTrue(hit("cases rose after COVID-19", "19"))
        self.assertTrue(hit("as reported in (2019)", "2019"))
        self.assertTrue(hit("(12)", "12", cell=True))  # a figure as shown in brackets, asked for plainly
        self.assertFalse(hit("12", "(12)", cell=True))  # asked for as negative, it must be negative
        self.assertFalse(hit("scores fell from 2019-2023", "-2023"))

    def test_limits_and_page_lists_must_name_something(self):
        with self.assertRaisesRegex(reader.ReadError, "at least 1"):
            reader.find(self.doc, "Revenue", limit=0)
        for spec in (",", " ", "p"):
            with self.assertRaisesRegex(reader.ReadError, "no pages"):
                reader.parse_pages(spec, 3)

    def test_bad_references_explain_themselves(self):
        for ref, message in (("x9", "not a page or block"), ("p9", "outside 1 to 3"), ("p1:b99", "has blocks")):
            with self.assertRaisesRegex(reader.ReadError, message):
                reader.table(self.doc, ref)
        with self.assertRaisesRegex(reader.ReadError, "is a heading"):
            reader.table(self.doc, "p1:b2")
        with self.assertRaisesRegex(reader.ReadError, "read at most 1 at a time"):
            reader.read(self.doc, "1-2", limit=1)


class UnitsTests(unittest.TestCase):
    def test_units_in_the_table_heading_or_in_the_singular(self):
        from reportlab.pdfgen import canvas

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "units.pdf"
            c = canvas.Canvas(str(path), pagesize=(612, 792))
            for heading, above in (("(In millions, except per share data)", None), ("", "Amounts in € million")):
                c.setFont("Helvetica", 10)
                if above:
                    c.drawString(72, 730, above)
                c.drawString(72, 700, heading)
                c.drawRightString(440, 700, "2024")
                c.drawRightString(520, 700, "2023")
                for k, (label, a, b) in enumerate((("Revenue", "7,259", "6,216"), ("Net income", "1,381", "(367)"))):
                    c.drawString(72, 680 - 16 * k, label)
                    c.drawRightString(440, 680 - 16 * k, a)
                    c.drawRightString(520, 680 - 16 * k, b)
                c.showPage()
            c.save()
            with Document(str(path), ocr="never", cache=False) as doc:
                for n in (1, 2):
                    k = next(k for k, b in enumerate(doc.page(n).blocks, 1) if b.kind == "table")
                    self.assertEqual(reader.table(doc, f"p{n}:b{k}")["units"], "millions", n)


class CacheTests(unittest.TestCase):
    def test_pages_are_read_once_and_cached_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch

            with patch("pdfscope.vision.CACHE", Path(tmp)):
                with Document(str(EXAMPLE), ocr="never") as doc:
                    first = page_markdown(doc.page(2))
                with (
                    Document(str(EXAMPLE), ocr="never") as doc,
                    patch.object(Document, "_read", side_effect=AssertionError),
                ):
                    self.assertEqual(page_markdown(doc.page(2)), first)

    def test_pages_cached_by_older_code_are_read_again(self):
        real = document.resources.files("pdfscope")

        class Edited:  # the same package with one line added to layout.py
            def joinpath(self, name):
                original = real.joinpath(name).read_bytes()
                return SimpleNamespace(read_bytes=lambda: original + (b"#" if name == "layout.py" else b""))

        with patch.object(document.resources, "files", return_value=Edited()):
            self.assertNotEqual(document._code_key(), document.CODE_KEY)
        with tempfile.TemporaryDirectory() as tmp, patch("pdfscope.vision.CACHE", Path(tmp)):
            with Document(str(EXAMPLE), ocr="never") as doc:
                doc.page(2)
            with patch.object(document, "CODE_KEY", "edited"), Document(str(EXAMPLE), ocr="never") as doc:
                with patch.object(Document, "_read", wraps=doc._read) as read:
                    doc.page(2)
                self.assertEqual(read.call_count, 1)

    def test_a_scanned_page_reads_the_same_whichever_page_is_read_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan = Path(tmp) / "scan.pdf"
            frames = [Image.new("RGB", (850, 1100), (250 - k, 250, 250)) for k in range(3)]
            frames[0].save(scan, "PDF", resolution=100, save_all=True, append_images=frames[1:])

            class PageOCR:  # tells the pages apart by their shade
                def read_image(self, image):
                    page = 250 - image.getpixel((5, 5))[0] + 1
                    lines = [("Results of operations", (0.1, 0.03, 0.4, 0.045))] if page == 2 else []
                    lines += [
                        (
                            f"Body line {k} of page {page} with a few ordinary words",
                            (0.1, 0.1 + 0.03 * k, 0.8, 0.115 + 0.03 * k),
                        )
                        for k in range(20)
                    ]
                    return ocr_lines(*lines)

                def close(self):
                    pass

            seen = []
            for order in ([2], [1, 2], [3, 2]):
                with Document(str(scan), cache=False, engine=PageOCR()) as doc:
                    for n in order:
                        page = doc.page(n)
                    seen.append(next(b.kind for b in page.blocks if b.text == "Results of operations"))
            # page 2's top line is not repeated on its neighbours, so it is content however the pages are read
            self.assertEqual(len(set(seen)), 1, seen)
            self.assertNotEqual(seen[0], "header")

    def test_a_scan_with_margins_is_a_page_not_a_figure(self):
        with tempfile.TemporaryDirectory() as tmp:
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas

            scan = Path(tmp) / "scan.pdf"
            c = canvas.Canvas(str(scan), pagesize=(612, 792))
            c.drawImage(ImageReader(Image.new("RGB", (520, 700), "white")), 46, 46, 520, 700)  # 75% of the page
            c.save()
            lines = [
                (f"Body line {k} of the scanned letter to shareholders", (0.1, 0.1 + 0.03 * k, 0.8, 0.115 + 0.03 * k))
                for k in range(20)
            ]
            with Document(str(scan), cache=False, engine=FakeOCR(ocr_lines(*lines))) as doc:
                page = doc.page(1)
            self.assertEqual(page.source, "ocr")
            self.assertEqual([b.kind for b in page.blocks if b.kind == "figure"], [])
            self.assertIn("Body line 3 of the scanned letter", page_markdown(page))

    def test_pages_whose_ocr_failed_are_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp, patch("pdfscope.vision.CACHE", Path(tmp)):
            scan = Path(tmp) / "scan.png"
            Image.new("RGB", (600, 800), "white").save(scan)
            with Document(str(scan), engine=FakeOCR(error="the OCR helper stopped")) as doc:
                page = doc.page(1)
                self.assertTrue(any(w.startswith("OCR failed") for w in page.warnings))
                self.assertFalse(doc._cache_file(1).exists())


if __name__ == "__main__":
    unittest.main()
