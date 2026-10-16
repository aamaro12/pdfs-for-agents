"""Command line: pdfscope outline | find | read | table | show FILE ..., or pdfscope mcp."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, reader, render
from .document import Document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdfscope",
        description="Read a PDF in steps: outline, find, read, table and show. Every block comes with the page "
        "and box it came from.",
    )
    parser.add_argument("--version", action="version", version=f"pdfscope {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    def step(name, summary):
        command = commands.add_parser(name, help=summary, description=summary)
        command.add_argument("file", help="a PDF, or an image (PNG, JPEG, TIFF and others)")
        command.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
        command.add_argument(
            "--ocr",
            default="auto",
            choices=["auto", "always", "never"],
            help="when to OCR a page: only where its text layer fails (auto), always or never",
        )
        command.add_argument("--languages", default="en-US", help="OCR languages, comma separated, or auto")
        return command

    step("outline", "Every page: how it was read, its headings and tables, and warnings")
    find = step("find", "Blocks and table cells containing the text, or figures that round to the number")
    find.add_argument("query")
    find.add_argument("--pages", help="search these pages only, such as 40-60")
    find.add_argument(
        "--limit", type=int, default=reader.FIND_LIMIT, help=f"most matches (default {reader.FIND_LIMIT})"
    )
    read = step("read", "Pages in reading order, each block tagged with its citation")
    read.add_argument("pages", help="pages such as 12, 12-14 or 3,7")
    table = step("table", "One table as a grid, with its figures parsed and its units")
    table.add_argument("ref", help="a table block such as p12:b3")
    fig = step("figure", "What a figure shows: a description, and for a chart its data as a table (needs [figures])")
    fig.add_argument("ref", help="a figure block such as p12:b3")
    show = step("show", "Save an image of a page or a block, to look at it or check a citation")
    show.add_argument("ref", help="a page such as p12 or a block such as p12:b3")
    show.add_argument("-o", "--out", default="pdfscope.png", help="where to save the PNG (default pdfscope.png)")
    serve = commands.add_parser("mcp", help="Serve the same steps as MCP tools over stdio")
    serve.add_argument("--root", default=".", help="the folder the server may read (default: this folder)")
    args = parser.parse_args(argv)

    if args.command == "mcp":
        try:
            from .server import build_server
        except ImportError:
            print(
                'pdfscope: the MCP server needs the mcp extra: pip install "pdfscope[mcp] @ git+https://github.com/aamaro12/pdfs-for-agents"',
                file=sys.stderr,
            )
            return 1
        build_server(args.root).run()
        return 0
    if not os.path.isfile(args.file):
        print(f"pdfscope: no such file: {args.file}", file=sys.stderr)
        return 1
    try:
        doc = Document(args.file, ocr=args.ocr, languages=args.languages)
    except Exception as error:  # an empty, damaged or password-locked file, or an image too large to open
        print(f"pdfscope: could not open {args.file}: {error}", file=sys.stderr)
        return 1
    try:
        with doc:
            if args.command == "outline":
                result, text = reader.outline(doc), render.outline_markdown
            elif args.command == "find":
                result, text = reader.find(doc, args.query, args.limit, args.pages), render.find_markdown
            elif args.command == "read":
                result, text = reader.read(doc, args.pages), render.read_markdown
            elif args.command == "table":
                result, text = reader.table(doc, args.ref), render.table_step_markdown
            elif args.command == "figure":
                result, text = reader.figure(doc, args.ref), render.figure_markdown
            else:
                result, text = reader.show(doc, args.ref, args.out), render.show_markdown
    except (reader.ReadError, OSError, ValueError) as error:
        print(f"pdfscope: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
