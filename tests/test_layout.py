"""Layout rules on hand-built spans: running headers, tables, headings, reading order."""

import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from pdfscope.document import Document
from pdfscope.layout import (
    _heading_units,
    _join,
    find_tables,
    grid,
    is_number,
    layout,
    margins,
    reading_order,
    rows_of,
    tidy,
)
from support import phrase, words


def statement(top=0.20):
    """A small income statement: dollar signs in their own column, date headings, a section label."""
    rows = [[phrase("Dec 31, 2026 Dec 31, 2025", 0.58, top)]]
    data = [
        ("Revenue", "$", "1,320", "$", "1,200"),
        ("Cost of revenue", "", "766", "", "696"),
        ("Operating expenses", None, None, None, None),
        ("Selling", "", "201", "", "187"),
        ("Net income", "$", "167", "$", "139"),
    ]
    for k, (label, d1, v1, d2, v2) in enumerate(data):
        y = top + 0.03 + 0.025 * k
        row = [phrase(label, 0.1, y)]
        if v1 is not None:
            if d1:
                row.append(phrase(d1, 0.56, y))
            row.append(phrase(v1, 0.66 - 0.008 * len(v1), y))
            if d2:
                row.append(phrase(d2, 0.71, y))
            row.append(phrase(v2, 0.81 - 0.008 * len(v2), y))
        rows.append(row)
    return rows


class LayoutTests(unittest.TestCase):
    def test_numbers(self):
        for t in ("1,320", "(48)", "12.5%", "$ 60,922", "-3.1", "—", "0.569"):
            self.assertTrue(is_number(t), t)
        for t in ("Revenue", "Q1 2025", "Note 4"):
            self.assertFalse(is_number(t), t)
        self.assertEqual(tidy("2,507_"), "2,507")
        self.assertEqual(tidy("$ 4,368 $"), "4,368")

    def test_rows_group_spans_on_one_line(self):
        spans = words("Net", 0.1, 0.5) + words("income", 0.14, 0.501) + words("167", 0.6, 0.5)
        self.assertEqual([[s.text for s in r] for r in rows_of(spans)], [["Net", "income", "167"]])

    def test_a_statement_becomes_a_clean_table(self):
        rows = statement()
        self.assertEqual(find_tables(rows), [(0, 6)])
        g = grid(rows)
        self.assertEqual(g[0], ["", "Dec 31, 2026", "Dec 31, 2025"])
        self.assertEqual(g[1], ["Revenue", "1,320", "1,200"])
        self.assertEqual(g[3], ["Operating expenses", "", ""])
        self.assertEqual(g[5], ["Net income", "167", "139"])

    def test_prose_is_not_a_table(self):
        rows = [
            [
                phrase("the company grew revenue in most markets", 0.1, 0.3 + 0.02 * k),
                phrase("while costs rose more slowly than sales", 0.55, 0.3 + 0.02 * k),
            ]
            for k in range(6)
        ]
        self.assertEqual(find_tables(rows), [])

    def test_aligned_names_are_a_table(self):
        people = [("Name", "Affiliation"), ("Ann Lee", "LB Sutton"), ("Raj Patel", "LB Croydon"), ("Jo Hart", "TfL")]
        rows = [[phrase(a, 0.1, 0.3 + 0.02 * k), phrase(b, 0.5, 0.3 + 0.02 * k)] for k, (a, b) in enumerate(people)]
        self.assertEqual(find_tables(rows), [(0, 4)])
        self.assertEqual(grid(rows)[2], ["Raj Patel", "LB Croydon"])

    def test_heading_units_split_run_together_dates(self):
        units = _heading_units(words("Jan 28, 2024 Jan 29, 2023", 0.6, 0.1))
        self.assertEqual([" ".join(w.text for w in u) for u in units], ["Jan 28, 2024", "Jan 29, 2023"])
        units = _heading_units(words("FY2025 FY2024", 0.6, 0.1))
        self.assertEqual(len(units), 2)

    def test_page_numbers_and_edge_lines_are_margins(self):
        rows = [[phrase("Annual Report 2026", 0.1, 0.03)], [phrase("Results", 0.1, 0.15, height=0.02, bold=True)]]
        rows += [[phrase("Body text line number %d of the page" % k, 0.1, 0.2 + 0.02 * k)] for k in range(10)]
        rows += [[phrase("14", 0.5, 0.96)]]
        header, body, footer = margins(rows)
        self.assertEqual([r[0].text for r in header], ["Annual Report 2026"])
        self.assertEqual([r[0].text for r in footer], ["14"])
        self.assertEqual(len(body), 11)

    def test_known_running_headers_need_repeating(self):
        rows = [[phrase("Chapter title", 0.1, 0.03)]] + [[phrase("text %d" % k, 0.1, 0.2 + 0.02 * k)] for k in range(5)]
        header, _, _ = margins(rows, repeated=set())
        self.assertEqual(header, [])  # nearby pages do not repeat it, so it stays as content
        header, _, _ = margins(rows, repeated={"chapter title"})
        self.assertEqual(len(header), 1)

    def test_two_columns_read_left_then_right_after_the_title(self):
        spans = [phrase("A wide title over both columns", 0.1, 0.1)]
        spans += [phrase("left %d" % k, 0.1, 0.2 + 0.02 * k) for k in range(8)]
        spans += [phrase("right %d" % k, 0.55, 0.2 + 0.02 * k) for k in range(8)]
        runs = reading_order(spans)
        order = [s.text for run in runs for s in sorted(run, key=lambda s: s.y0)]
        self.assertEqual(order[0], "A wide title over both columns")
        self.assertEqual(order[1:9], ["left %d" % k for k in range(8)])
        self.assertEqual(order[9:], ["right %d" % k for k in range(8)])

    def test_a_page_number_in_the_gutter_does_not_hide_the_columns(self):
        spans = [phrase("left %d" % k, 0.1, 0.2 + 0.02 * k) for k in range(10)]
        spans += [phrase("right %d" % k, 0.52, 0.2 + 0.02 * k) for k in range(10)]
        spans += [phrase("161", 0.485, 0.5)]  # centred under the gutter
        order = [s.text for run in reading_order(spans) for s in sorted(run, key=lambda s: s.y0)]
        self.assertEqual(order[:10], ["left %d" % k for k in range(10)])
        self.assertEqual(order[10:20], ["right %d" % k for k in range(10)])
        self.assertEqual(order[20], "161")

    def test_prose_with_years_is_not_a_table(self):
        left = [
            "According to the Bureau of",
            "Immigration statistics, from",
            "the number tripled in 2012,",
            "accounting for 61,000 students",
        ]
        right = [
            "the researcher conducted this",
            "study to answer the question",
            "of how students adapt in 2013",
            "and what the schools provide",
        ]
        rows = [
            [phrase(a, 0.1, 0.3 + 0.02 * k), phrase(b, 0.55, 0.3 + 0.02 * k)]
            for k, (a, b) in enumerate(zip(left, right))
        ]
        self.assertEqual(find_tables(rows), [])

    def test_layout_assigns_kinds(self):
        spans = [phrase("Results for the year", 0.1, 0.12, height=0.03)]
        spans += [phrase("Revenue grew in all regions and margins held", 0.1, 0.2)]
        spans += [phrase("• Paid down the term loan", 0.1, 0.25)]
        spans += [s for r in statement(top=0.4) for s in r]
        kinds = [b.kind for b in layout(spans)]
        self.assertEqual(kinds[0], "heading")
        self.assertIn("list", kinds)
        self.assertIn("table", kinds)

    def test_a_chart_just_below_a_paragraph_is_kept(self):
        line = "Revenue grew in every region this year and the two largest markets grew fastest"
        spans = [phrase(line, 0.1, y) for y in (0.26, 0.28, 0.30)]
        labels = [("North", 0.15, 0.55), ("South", 0.35, 0.55), ("310", 0.15, 0.36), ("285", 0.35, 0.38)]
        spans += [phrase(t, x, y) for t, x, y in labels]
        blocks = layout(spans, figures=[{"box": (0.1, 0.32, 0.7, 0.54), "source": "drawing"}])
        figure = next(b for b in blocks if b.kind == "figure")
        self.assertEqual(sorted(figure.info["labels"]), ["285", "310", "North", "South"])
        text = " ".join(b.text for b in blocks if b.kind == "text")
        self.assertEqual(text.count("largest markets"), 3)
        self.assertNotIn("310", text)

    def test_a_picture_in_the_right_column_sits_between_that_column_s_paragraphs(self):
        line = "a line of text in this column of the page"
        spans = [phrase(f"{line} L1", 0.08, y) for y in (0.20, 0.22, 0.24)]
        spans += [phrase(f"{line} L2", 0.08, 0.36 + 0.02 * k) for k in range(5)]
        spans += [phrase(f"{line} R1", 0.55, y) for y in (0.20, 0.22, 0.24)]
        spans += [phrase(f"{line} R2", 0.55, 0.48 + 0.02 * k) for k in range(5)]
        blocks = layout(spans, figures=[{"box": (0.55, 0.30, 0.92, 0.45), "source": "image"}])
        order = [b.text.split()[-1] if b.kind == "text" else b.kind for b in blocks]
        self.assertEqual(order, ["L1", "L2", "R1", "figure", "R2"])

    def test_hyphenated_compounds_keep_their_hyphens_across_lines(self):
        self.assertEqual(_join(["an order-", "of-magnitude change"]), "an order-of-magnitude change")
        self.assertEqual(_join(["a well-", "known brand"]), "a wellknown brand")  # a word broken at the line end
        self.assertEqual(_join(["dated by po-", "tassium-40 decay"]), "dated by potassium-40 decay")
        self.assertEqual(_join(["an ad-", "discor- dance"]), "an addiscor- dance")
        for lines, joined in (
            (["an order-of-", "magnitude change"], "an order-of-magnitude change"),
            (["a non-", "GAAP measure"], "a non-GAAP measure"),
            (["in end-to-", "end tests"], "in end-to-end tests"),
            (["an AI-", "defined product"], "an AI-defined product"),
            (["after COVID-", "19 cases"], "after COVID-19 cases"),
            (["from 2019-", "2023"], "from 2019-2023"),
            (["costs rose -", "which we expected"], "costs rose - which we expected"),
        ):
            self.assertEqual(_join(lines), joined)

    def test_a_short_last_line_above_a_chart_stays_in_its_paragraph(self):
        line = "Revenue grew in every region this year and the two largest markets grew fastest"
        spans = [phrase(line, 0.1, y) for y in (0.26, 0.28)]
        spans.append(phrase("as freight costs eased in the second half.", 0.1, 0.30))
        labels = [("North", 0.15, 0.55), ("South", 0.35, 0.55), ("310", 0.15, 0.36), ("285", 0.35, 0.38)]
        spans += [phrase(t, x, y) for t, x, y in labels]
        blocks = layout(spans, figures=[{"box": (0.1, 0.32, 0.7, 0.54), "source": "drawing"}])
        figure = next(b for b in blocks if b.kind == "figure")
        self.assertEqual(sorted(figure.info["labels"]), ["285", "310", "North", "South"])
        self.assertIn("in the second half.", " ".join(b.text for b in blocks if b.kind == "text"))


class PageTests(unittest.TestCase):
    """Whole pages drawn with reportlab and read from their text layer."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def read(self, name, draw, pages=1):
        path = self.dir / name
        c = canvas.Canvas(str(path), pagesize=(612, 792))
        for n in range(1, pages + 1):
            draw(c, n)
            c.showPage()
        c.save()
        doc = Document(str(path), ocr="never", cache=False)
        self.addCleanup(doc.close)
        return doc

    def test_a_statement_underlined_under_its_figures_keeps_its_row_labels(self):
        rows = [
            ("Subscription services", "6,603", "5,567", "4,546", False),
            ("Professional services", "656", "649", "593", True),
            ("Total revenues", "7,259", "6,216", "5,139", False),
            ("Costs of revenues", "1,771", "1,715", "1,428", False),
            ("Product development", "2,464", "2,271", "1,879", True),
            ("Total costs and expenses", "4,235", "3,986", "3,307", True),
            ("Operating income", "3,024", "2,230", "1,832", False),
        ]

        def draw(c, n):
            c.setFont("Helvetica", 10)
            c.drawCentredString(470, 716, "Year Ended January 31,")
            c.line(384, 712, 560, 712)
            for x, year in ((440, "2024"), (500, "2023"), (560, "2022")):
                c.drawRightString(x, 700, year)
                c.line(x - 56, 696, x, 696)  # a rule under each column heading, not under the labels
            y = 680
            for label, *figures, underline in rows:
                c.drawString(72, y, label)
                for x, figure in zip((440, 500, 560), figures):
                    c.drawRightString(x, y, figure)
                    if underline:
                        c.line(x - 56, y - 4, x, y - 4)  # a subtotal rule under the figures only
                y -= 18

        doc = self.read("statement.pdf", draw)
        table = next(b for b in doc.page(1).blocks if b.kind == "table")
        self.assertIn(["Total revenues", "7,259", "6,216", "5,139"], table.rows)
        self.assertIn(["Operating income", "3,024", "2,230", "1,832"], table.rows)

    def test_a_ruled_table_beside_justified_prose_stays_a_ruled_table(self):
        prose = "the samples were cleaned and the mineral content was measured for each of them in turn".split()

        def draw(c, n):
            c.setFont("Helvetica", 9)
            for k in range(24):  # a justified column on the left: every line ends at x = 290
                words = prose[k % 5 : k % 5 + 9]
                width = sum(c.stringWidth(w, "Helvetica", 9) for w in words)
                space, x = (218 - width) / (len(words) - 1), 72
                for w in words:
                    c.drawString(x, 720 - 13 * k, w)
                    x += c.stringWidth(w, "Helvetica", 9) + space
            c.line(320, 690, 560, 690)  # a ruled table on the right, rules across all its columns
            c.drawString(330, 680, "No")
            c.drawString(380, 680, "Element")
            c.drawString(470, 680, "Weight %")
            c.line(320, 674, 560, 674)
            for k, (element, weight) in enumerate((("O", "56.53"), ("Na", "0.24"), ("Mg", "0.29"), ("Al", "5.44"))):
                y = 662 - 13 * k
                c.drawString(330, y, str(k + 1))
                c.drawString(380, y, element)
                c.drawString(470, y, weight)
            c.line(320, 604, 560, 604)

        doc = self.read("paper.pdf", draw)
        table = next(b for b in doc.page(1).blocks if b.kind == "table")
        self.assertEqual(table.rows[0], ["No", "Element", "Weight %"])
        self.assertIn(["2", "Na", "0.24"], table.rows)

    def test_running_headers_a_little_way_from_the_edge_are_found(self):
        def draw(c, n):
            c.setFont("Helvetica", 9)
            c.drawString(72, 738, "Northwind Trading Annual Report 2026")  # about 7 to 8% from the top
            c.drawString(72, 60, f"Northwind Trading | Confidential | Page {n} of 3")  # about 9% from the bottom
            c.setFont("Helvetica", 10)
            for k in range(30):
                c.drawString(72, 680 - 14 * k, f"Body line {k} on page {n} of the report, with some ordinary words.")

        doc = self.read("band.pdf", draw, pages=3)
        kinds = {b.text: b.kind for b in doc.page(2).blocks}
        self.assertEqual(kinds["Northwind Trading Annual Report 2026"], "header")
        self.assertEqual(kinds["Northwind Trading | Confidential | Page 2 of 3"], "footer")


if __name__ == "__main__":
    unittest.main()
