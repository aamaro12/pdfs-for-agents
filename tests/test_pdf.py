"""The text layer: words with boxes, and the checks that decide whether it can be trusted."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from reportlab.pdfgen import canvas

from pdfscope.pdf import PDF, TextLayer, phrases, plausible
from support import EXAMPLE, make_northwind


class TextLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "northwind.pdf"
        make_northwind.build(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_words_have_boxes_as_page_fractions(self):
        with PDF(str(self.path)) as pdf:
            layer = pdf.text(1)
        self.assertEqual(layer.state, "good")
        title = [w for w in layer.words if w.y1 < 0.2 and w.text in ("A", "year", "of", "steady", "growth")]
        self.assertEqual([w.text for w in title], ["A", "year", "of", "steady", "growth"])
        for w in layer.words:
            self.assertTrue(0 <= w.x0 < w.x1 <= 1 and 0 <= w.y0 < w.y1 <= 1, w)
        # the title sits near the top, the page number near the bottom
        self.assertLess(title[0].y1, 0.2)
        self.assertGreater(next(w for w in layer.words if w.text == "1").y0, 0.9)

    def test_spans_split_at_column_gaps(self):
        with PDF(str(self.path)) as pdf:
            spans = pdf.text(1).spans
        texts = [s.text for s in spans]
        self.assertIn("Northwind Trading sells specialty foods to", texts)
        self.assertIn("Operating costs rose more slowly than", texts)

    def test_page_count_and_size(self):
        with PDF(str(EXAMPLE)) as pdf:
            self.assertEqual(pdf.pages, 3)
            self.assertEqual(pdf.size(1), (612.0, 792.0))
            self.assertEqual(pdf.render(1, dpi=72).size, (612, 792))

    def test_an_empty_page_has_no_usable_text(self):
        path = Path(self.tmp.name) / "blank.pdf"
        c = canvas.Canvas(str(path))
        c.showPage()
        c.save()
        with PDF(str(path)) as pdf:
            self.assertEqual(pdf.text(1).state, "empty")

    def test_a_shuffled_font_map_is_not_plausible(self):
        good = "The board approved the dividend and the results were in line with the plan for the year " * 3
        # NVIDIA's 2024 annual review: each letter mapped to another, "Our worldwide business activities..."
        shuffled = "7WT YQTNFYKFG DWUKPGUU CEVKXKVKGU CTG UWDLGEV VQ XCTKQWU NCYU TWNGU CPF TGIWNCVKQPU QH VJG " * 3
        self.assertTrue(plausible(good))
        self.assertFalse(plausible(shuffled))
        self.assertTrue(plausible("短い文章です" * 20))  # other scripts are not judged
        self.assertTrue(
            plausible("TOTAL ASSETS 65,728 41,182 TOTAL LIABILITIES 22,750 19,081 " * 5)
        )  # capitals with figures

    def test_unmapped_characters_mark_the_layer_broken(self):
        layer = TextLayer([], chars=100, broken=40)
        self.assertEqual(layer.state, "broken")

    def test_phrases_scale_gaps_by_page_shape(self):
        from support import words

        ws = words("Revenue", 0.1, 0.5) + words("1,320", 0.4, 0.5)
        self.assertEqual([s.text for s in phrases(ws)], ["Revenue", "1,320"])
        self.assertEqual([s.text for s in phrases(words("Cost of revenue", 0.1, 0.5))], ["Cost of revenue"])

    def test_a_bar_chart_with_gaps_between_its_bars_is_one_drawing(self):
        path = Path(self.tmp.name) / "bars.pdf"
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        for k, height in enumerate((80, 120, 60, 150, 100)):  # 24 pt bars, 30 pt (0.05 of the page) apart
            c.rect(100 + 54 * k, 300, 24, height, stroke=0, fill=1)
        c.save()
        with PDF(str(path)) as pdf:
            self.assertEqual(len(pdf.drawings(1)), 1)

    def test_printers_marks_off_the_page_are_not_a_chart(self):
        path = Path(self.tmp.name) / "marks.pdf"
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        for x in (20, 32, 600):  # bleed strips running past the top and bottom of the page
            c.rect(x, -20, 10, 832, stroke=0, fill=1)
        for r in (7, 5):  # a registration mark at the edge
            c.circle(30, 400, r, stroke=1, fill=0)
        c.setFont("Helvetica", 10)
        c.drawString(72, 700, "Body text of the page, which no chart should take in.")
        c.save()
        with PDF(str(path)) as pdf:
            self.assertEqual(pdf.drawings(1), [])

    def test_a_rotated_page_reads_as_shown(self):
        path = Path(self.tmp.name) / "rotated.pdf"
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        c.translate(500, 100)
        c.rotate(90)  # drawn sideways, as a landscape page often is, and shown upright by /Rotate 90
        c.setFont("Helvetica", 18)
        c.drawString(0, 0, "Quarterly revenue rose to 1,320")
        c.save()
        doc = pdfium.PdfDocument(str(path))
        doc[0].set_rotation(90)
        doc.save(str(path))
        doc.close()
        with PDF(str(path)) as pdf:
            spans = pdf.text(1).spans
            self.assertEqual(pdf.size(1), (792.0, 612.0))
            page = pdf.render(1, 72)
        self.assertEqual([s.text for s in spans], ["Quarterly revenue rose to 1,320"])
        # the box is where the line shows on the landscape page: ink inside it, none just below it
        x0, y0, x1, y1 = (round(v * size) for v, size in zip(spans[0].box, (792, 612, 792, 612)))
        self.assertLess(min(page.crop((x0, y0, x1, y1)).convert("L").getdata()), 100)
        self.assertGreater(min(page.crop((x0, y1 + 4, x1, y1 + 30)).convert("L").getdata()), 200)

    def test_the_crop_box_is_the_page(self):
        path = Path(self.tmp.name) / "cropped.pdf"
        doc = pdfium.PdfDocument(str(self.path))
        for page in doc:
            page.set_cropbox(36, 60, 576, 732)  # trimmed: the running header and page number fall outside
        doc.save(str(path))
        doc.close()
        with PDF(str(self.path)) as full, PDF(str(path)) as cropped:
            before = next(w for w in full.text(1).words if w.text == "growth")
            after = next(w for w in cropped.text(1).words if w.text == "growth")
            self.assertAlmostEqual(after.x0, (before.x0 * 612 - 36) / 540, places=3)
            self.assertAlmostEqual(after.y0, (before.y0 * 792 - 60) / 672, places=3)
            words = [w.text for w in cropped.text(1).words]
            self.assertTrue("Annual" not in words and "1" not in words)  # the running header and page number
            chart, shown = full.drawings(3)[0], cropped.drawings(3)[0]
            self.assertAlmostEqual(shown[0], (chart[0] * 612 - 36) / 540, places=3)

    def test_a_media_box_inside_the_crop_box_is_the_page(self):
        path = Path(self.tmp.name) / "media.pdf"
        doc = pdfium.PdfDocument(str(self.path))
        for page in doc:
            page.set_mediabox(36, 60, 576, 732)  # the page is where the media box and crop box overlap
            page.set_cropbox(0, 0, 612, 792)
        doc.save(str(path))
        doc.close()
        with PDF(str(self.path)) as full, PDF(str(path)) as trimmed:
            self.assertEqual(trimmed.size(1), (540.0, 672.0))
            before = next(w for w in full.text(1).words if w.text == "growth")
            after = next(w for w in trimmed.text(1).words if w.text == "growth")
            self.assertAlmostEqual(after.x0, (before.x0 * 612 - 36) / 540, places=3)
            self.assertIn("freight", [w.text for w in trimmed.text(1).words])

    def test_rounded_boxes_are_not_charts(self):
        path = Path(self.tmp.name) / "cards.pdf"
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        for k, (label, value) in enumerate([("Revenue", "$60.9bn"), ("Net income", "$29.8bn"), ("Cash", "$27.0bn")]):
            c.setFillGray(0.95)
            c.roundRect(72 + 160 * k, 560, 140, 110, 10, stroke=1, fill=1)  # a card with rounded corners
            c.setFillGray(0)
            c.drawString(84 + 160 * k, 645, label)
            c.drawString(84 + 160 * k, 610, value)
        c.save()
        with PDF(str(path)) as pdf:
            self.assertEqual(pdf.drawings(1), [])

    def test_bar_charts_side_by_side_stay_two_charts(self):
        for name, places in (("pair", ((72, 400), (320, 400))), ("stacked", ((100, 560), (100, 380)))):
            path = Path(self.tmp.name) / f"{name}.pdf"
            c = canvas.Canvas(str(path), pagesize=(612, 792))
            for x0, base in places:
                for k, height in enumerate((80, 120, 60, 150, 100)):
                    c.rect(x0 + 44 * k, base, 30, height * 0.9 if name == "stacked" else height, stroke=0, fill=1)
            c.save()
            with PDF(str(path)) as pdf:
                self.assertEqual(len(pdf.drawings(1)), 2, name)

    def test_shaded_table_cells_are_not_bars(self):
        path = Path(self.tmp.name) / "roster.pdf"
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        for k in range(8):  # its heading drawn as letter outlines, filled or stroked: small shapes with slanted edges
            x = 170 + 20 * k
            letter = c.beginPath()
            for px, py in ((x, 680), (x + 7, 710), (x + 14, 680), (x + 10, 680), (x + 7, 698), (x + 4, 680)):
                letter.lineTo(px, py) if px != x or py != 680 else letter.moveTo(px, py)
            letter.close()
            c.drawPath(letter, stroke=k % 2, fill=1 - k % 2)
        for row in range(4):  # a timetable: shaded cells of one size, evenly spaced, with text in them
            for col in range(5):
                c.setFillGray(0.85)
                c.rect(160 + 84 * col, 640 - 30 * row, 76, 24, stroke=0, fill=1)
                c.setFillGray(0)
                c.drawString(164 + 84 * col, 648 - 30 * row, "9am-1.30pm")
        c.save()
        with PDF(str(path)) as pdf:
            self.assertEqual(pdf.drawings(1), [])

    def test_image_files_follow_their_orientation_and_show_on_white(self):
        rotated = Path(self.tmp.name) / "phone.jpg"
        exif = Image.Exif()
        exif[0x0112] = 6  # taken with the phone turned: shown 90 degrees clockwise
        Image.new("RGB", (400, 300), "white").save(rotated, exif=exif)
        with PDF(str(rotated)) as pdf:
            width, height = pdf.size(1)
            self.assertGreater(height, width)
        clear = Path(self.tmp.name) / "clear.png"
        image = Image.new("RGBA", (200, 100), (0, 0, 0, 0))  # transparent pixels stored as black
        image.paste((0, 0, 0, 255), (20, 20, 60, 60))
        image.save(clear)
        with PDF(str(clear)) as pdf:
            page = pdf.render(1, 72)
            self.assertGreater(min(page.getpixel((page.width - 5, page.height - 5))), 240)
            self.assertLess(max(page.getpixel((int(page.width * 0.2), int(page.height * 0.4)))), 20)

    def test_tools_running_at_once_do_not_crash(self):
        script = textwrap.dedent(
            f"""
            import asyncio, os, shutil
            os.environ["PDFSCOPE_NO_CACHE"] = "1"
            from reportlab.pdfgen import canvas
            from pdfscope.server import build_server
            root = {str(Path(self.tmp.name) / "parallel")!r}
            os.makedirs(root, exist_ok=True)
            c = canvas.Canvas(os.path.join(root, "long.pdf"))
            line = "revenue grew in the quarter and the company kept its margin steady."
            for n in range(1, 241):  # dense pages, so calls overlap inside pdfium
                c.drawString(72, 790, "Section %d: results for the period" % n)
                for k in range(50):
                    c.drawString(72, 770 - 14.5 * k, "Line %d of page %d: %s" % (k, n, line))
                c.showPage()
            c.save()
            async def call(server, name, **args):
                try:
                    await server.call_tool(name, args)
                    return 0
                except Exception:
                    return 1
            async def main():
                server = build_server(root)
                jobs = [("read", dict(file="long.pdf", pages="%d-%d" % (p, p + 2))) for p in range(1, 90, 3)]
                jobs += [("find", dict(file="long.pdf", query="Section 4", pages="1-120"))] * 4
                jobs += [("show", dict(file="long.pdf", ref="p%d:b2" % p)) for p in range(1, 30)]
                failed = sum(await asyncio.gather(*[call(server, n, **a) for n, a in jobs]))
                print("failed", failed)
            for _ in range(2):
                asyncio.run(main())
            """
        )
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=600)
        self.assertEqual(
            (done.returncode, done.stdout.split()), (0, ["failed", "0", "failed", "0"]), done.stderr[-500:]
        )


if __name__ == "__main__":
    unittest.main()
