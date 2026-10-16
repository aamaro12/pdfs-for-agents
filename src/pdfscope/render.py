"""Markdown for pages and for each step's result."""

from __future__ import annotations

from .document import Page
from .layout import Block

SOURCE = {"text layer": "text layer", "ocr": "OCR", "none": "no text"}


def page_ranges(numbers: list[int]) -> str:
    """[2, 3, 4, 9] as '2-4, 9'."""
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(f"{a}-{b}" if b > a else f"{a}" for a, b in runs)


def table_markdown(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [[c.replace("|", "\\|").replace("\n", " ") for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def block_markdown(b: Block) -> str:
    if b.kind == "figure":
        return ""  # a figure's caption is its own text block; its labels are not running text
    if b.kind == "heading":
        return "#" * b.level + " " + b.text
    if b.kind == "list":
        return "- " + b.text
    if b.kind == "table":
        return table_markdown(b.rows)
    return b.text


def page_markdown(page: Page, margins: bool = False) -> str:
    """The page's blocks in reading order. Running headers, footers and page numbers are left out unless asked."""
    parts = [block_markdown(b) for b in page.blocks if margins or b.kind not in ("header", "footer")]
    return "\n\n".join(p for p in parts if p.strip())


def _label(page: int, label) -> str:
    return f"p{page}" + (f" (printed {label})" if label and label != str(page) else "")


def outline_markdown(o: dict) -> str:
    read = ", ".join(f"{SOURCE.get(k, k)}: {v}" for k, v in o["sources"].items())
    lines = [
        f"**{o['file']}** · sha256 {o['sha256']} · {o['pages']} pages · read from {read}",
        f"Cite blocks like [{o['file']}] p1:b2.",
        "",
    ]
    rows = []
    for p in o["outline"]:
        heads = "; ".join(f"{t[:60]} ({c})" for c, t in p["headings"])
        tables = "; ".join(
            f"{t['cite']} {t['rows']}×{t['columns']}"
            + (f": {', '.join(t['first_row'])[:50]}" if t["first_row"] else "")
            for t in p["tables"]
        )
        figs = "; ".join(f["cite"] + (f": {f['caption'][:40]}" if f["caption"] else "") for f in p.get("figures", []))
        rows.append(
            [
                _label(p["page"], p["label"]),
                SOURCE.get(p["source"], p["source"]),
                heads or (p["starts"][:60] + ("…" if len(p["starts"]) > 60 else "")),
                tables,
                figs,
            ]
        )
    head = ["Page", "Read from", "Headings, or how the page starts", "Tables", "Figures"]
    lines.append(table_markdown([head] + rows))
    if o.get("unread"):
        lines += [
            "",
            f"Pages {page_ranges(o['unread'])} not read yet (OCR of scanned pages takes time): call outline again "
            "to continue; pages read so far are kept.",
        ]
    warned = [p for p in o["outline"] if p["warnings"]]
    if warned:
        lines += ["", "Warnings:"]
        lines += [f"- {_label(p['page'], p['label'])}: {'; '.join(p['warnings'])}" for p in warned[:20]]
        if len(warned) > 20:
            lines.append(f"- and {len(warned) - 20} more pages")
    return "\n".join(lines)


def find_markdown(f: dict) -> str:
    unread = ""
    if f.get("unread"):
        unread = (
            f"\n\nPages {page_ranges(f['unread'])} not searched yet (OCR of scanned pages takes time): call find "
            "again to search them."
        )
    if not f["hits"]:
        return f"Nothing matches {f['query']!r}." + unread
    rows = [["Cite", "Kind", "Text", "Row", "Column"]]
    for h in f["hits"]:
        rows.append(
            [
                h["cite"] + (f" (printed {h['label']})" if h.get("label") and h["label"] != str(h["page"]) else ""),
                h["kind"],
                h["text"],
                h.get("row", ""),
                h.get("column", ""),
            ]
        )
    more = "\n\nMore matches exist; narrow the query or the pages." if f["more"] else ""
    return table_markdown(rows) + more + unread


def read_markdown(r: dict) -> str:
    out = []
    for p in r["pages"]:
        out.append(f"## {_label(p['page'], p['label'])} · read from {SOURCE.get(p['source'], p['source'])}")
        for w in p["warnings"]:
            out.append(f"> {w}")
        for b in p["blocks"]:
            tag = f"[{b['cite']}]"
            if b["kind"] in ("header", "footer"):
                out.append(f"*{b['kind'].capitalize()}:* {b['text']} {tag}")
            elif b["kind"] == "table":
                out.append(f"{tag} table, {len(b['rows'])} rows\n\n" + table_markdown(b["rows"]))
            elif b["kind"] == "figure":
                what = {"drawing": "a drawn chart or diagram", "image": "an image", "image file": "the image"}
                caption = f": {b['text']}" if b["text"] else ""
                out.append(f"{tag} figure, {what.get(b.get('source'), 'an image')}{caption} (use figure {b['cite']})")
            elif b["kind"] == "heading":
                out.append("#" * (b["level"] + 2) + f" {b['text']} {tag}")
            elif b["kind"] == "list":
                out.append(f"- {b['text']} {tag}")
            else:
                out.append(f"{b['text']} {tag}")
    return "\n\n".join(out)


def table_step_markdown(t: dict) -> str:
    printed = f" (printed page {t['label']})" if t["label"] and t["label"] != str(t["page"]) else ""
    head = f"Table {t['cite']}{printed}" + (f" · figures in {t['units']}" if t["units"] else "")
    figures = sum(v is not None for row in t["values"] for v in row)
    return f"{head}\n\n{table_markdown(t['rows'])}\n\n{figures} cells read as figures; brackets are negative."


def figure_markdown(f: dict) -> str:
    head = f"Figure {f['cite']}" + (f": {f['caption']}" if f["caption"] else "")
    lines = [head, "", f"Looks like: {f['description']}"]
    if f["table"]:
        lines += ["", "Data read from the chart:", "", table_markdown(f["table"])]
        if f.get("unconfirmed"):
            lines += ["", "Not among the numbers printed on the chart, so check these: " + ", ".join(f["unconfirmed"])]
        elif f.get("unconfirmed") == []:
            lines += [
                "",
                "Every figure in the table appears among the numbers printed on the chart (which series and "
                "column each belongs to is not checked).",
            ]
    if f["labels"]:
        where = "the PDF's text, exact" if f.get("labels_from") == "text layer" else "read by OCR"
        lines += ["", f"Text inside the figure ({where}): " + " · ".join(f["labels"][:40])]
    lines += ["", f["note"]]
    return "\n".join(lines)


def show_markdown(s: dict) -> str:
    return f"Saved an image of {s['cite']} to {s['image']} ({s['width']}×{s['height']} px)."
