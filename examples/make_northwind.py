"""Build examples/northwind.pdf: three pages of a made-up annual report (needs reportlab).

Page 1 has a title, two columns of text and a running header and footer. Page 2 has the income
statement: a table with dollar signs in their own column, date headings and section labels. Page 3 has
a bar chart drawn as vector graphics, with its values printed on the bars and a caption.
"""

import sys
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

W, H = LETTER

LEFT = [
    "Northwind Trading sells specialty foods to",
    "restaurants and grocers in twelve countries.",
    "Revenue grew 10% in fiscal 2026 as volumes",
    "recovered in our two largest markets, and",
    "gross margin held at 42% despite higher",
    "freight costs in the second half.",
]
RIGHT = [
    "Operating costs rose more slowly than",
    "revenue, so operating income increased to",
    "$235 thousand. We paid down the remaining",
    "term loan in March and ended the year",
    "with no borrowings and $1.2 million of",
    "cash on hand.",
]
ROWS = [
    ("Revenue", "$", "1,320", "$", "1,200"),
    ("Cost of revenue", "", "766", "", "696"),
    ("Gross profit", "", "554", "", "504"),
    ("Operating expenses", None, None, None, None),
    ("Selling and marketing", "", "201", "", "187"),
    ("General and administrative", "", "118", "", "113"),
    ("Total operating expenses", "", "319", "", "300"),
    ("Operating income", "", "235", "", "204"),
    ("Interest expense", "", "(12)", "", "(18)"),
    ("Income before tax", "", "223", "", "186"),
    ("Income tax", "", "(56)", "", "(47)"),
    ("Net income", "$", "167", "$", "139"),
]


REGIONS = ["North", "South", "East", "West", "Online"]
REGIONS_2026 = [310, 285, 240, 205, 280]
REGIONS_2025 = [280, 262, 228, 196, 234]


def margins(c, page):
    c.setFont("Helvetica", 8)
    c.drawString(72, H - 40, "Northwind Trading · Annual Report 2026")
    c.drawRightString(W - 72, 40, f"{page}")


def build(path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.setTitle("Northwind Trading annual report 2026")

    margins(c, 1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(72, H - 100, "A year of steady growth")
    c.setFont("Helvetica", 10)
    for k, (left, right) in enumerate(zip(LEFT, RIGHT)):
        y = H - 140 - 14 * k
        c.drawString(72, y, left)
        c.drawString(320, y, right)
    c.showPage()

    margins(c, 2)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(W / 2, H - 90, "Northwind Trading")
    c.drawCentredString(W / 2, H - 105, "Consolidated Statement of Income")
    c.setFont("Helvetica", 10)
    c.drawCentredString(W / 2, H - 120, "(In thousands of dollars)")
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(430, H - 150, "Dec 31, 2026")
    c.drawRightString(520, H - 150, "Dec 31, 2025")
    c.setFont("Helvetica", 10)
    for k, (label, d1, v1, d2, v2) in enumerate(ROWS):
        y = H - 170 - 16 * k
        c.drawString(72 if v1 is not None else 72, y, label)
        if v1 is None:
            continue
        if d1:
            c.drawString(372, y, d1)
        c.drawRightString(430, y, v1)
        if d2:
            c.drawString(462, y, d2)
        c.drawRightString(520, y, v2)
    c.showPage()

    margins(c, 3)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, H - 90, "Revenue by region")
    c.setFont("Helvetica", 10)
    c.drawString(72, H - 110, "Every region grew, and the two largest markets grew fastest.")
    chart = Drawing(400, 230)
    bars = VerticalBarChart()
    bars.x, bars.y, bars.width, bars.height = 40, 30, 340, 170
    bars.data = [REGIONS_2026, REGIONS_2025]
    bars.categoryAxis.categoryNames = REGIONS
    bars.valueAxis.valueMin, bars.valueAxis.valueMax, bars.valueAxis.valueStep = 0, 400, 100
    bars.bars[0].fillColor, bars.bars[1].fillColor = colors.HexColor("#2f6f4f"), colors.HexColor("#a7c4b5")
    bars.barLabelFormat = "%d"
    bars.barLabels.nudge = 7
    chart.add(bars)
    legend = Legend()
    legend.x, legend.y, legend.alignment = 300, 215, "right"
    legend.colorNamePairs = [(bars.bars[0].fillColor, "2026"), (bars.bars[1].fillColor, "2025")]
    chart.add(legend)
    renderPDF.draw(chart, c, 90, H - 380)
    c.drawString(72, H - 400, "Figure 1: Revenue by region, 2026 and 2025 (thousands of dollars)")
    c.showPage()
    c.save()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("northwind.pdf")
    build(out)
    print(out)
