"""Write pdfscope's Markdown for olmOCR-bench, ready for the benchmark's own scorer.

    python bench/run_olmocr.py --data bench/data/bench_data --name pdfscope
    python -m olmocr.bench.benchmark --dir bench/data/bench_data --candidate pdfscope

The scorer lives in the `olmocr[bench]` package (install it in its own environment). Outputs go to
<data>/<name>/<category>/<pdf name>_pg1_repeat1.md, the layout the scorer expects.
"""

import argparse
import collections
import glob
import json
import os
import statistics
import time

from pdfscope import vision
from pdfscope.document import Document
from pdfscope.render import page_markdown

CATEGORIES = (
    "tables",
    "old_scans",
    "multi_column",
    "headers_footers",
    "long_tiny_text",
    "arxiv_math",
    "old_scans_math",
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="the benchmark's bench_data folder")
    ap.add_argument("--name", default="pdfscope", help="candidate folder name")
    ap.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    ap.add_argument("--categories", default="tables,old_scans,multi_column,headers_footers,long_tiny_text")
    ap.add_argument("--force", action="store_true", help="rewrite outputs that already exist")
    args = ap.parse_args()

    times, sources, failures = [], collections.Counter(), []
    engine = vision.Vision() if args.ocr != "never" and vision.available() is None else None
    for cat in args.categories.split(","):
        for path in sorted(glob.glob(os.path.join(args.data, "pdfs", cat, "*.pdf"))):
            rel = os.path.relpath(path, os.path.join(args.data, "pdfs"))[:-4]
            out = os.path.join(args.data, args.name, f"{rel}_pg1_repeat1.md")
            if os.path.exists(out) and not args.force:
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            t0 = time.time()
            try:
                with Document(path, ocr=args.ocr, engine=engine, cache=False) as doc:
                    page = doc.page(1)
                    text = page_markdown(page)
                    sources[page.source] += 1
            except Exception as e:  # a page that fails scores zero, as it would for a user
                text = ""
                failures.append(f"{rel}: {type(e).__name__}: {e}"[:200])
            times.append(time.time() - t0)
            with open(out, "w", errors="replace") as f:
                f.write(text)
    if times:
        print(
            json.dumps(
                {
                    "pages": len(times),
                    "mean_s": round(statistics.mean(times), 3),
                    "median_s": round(statistics.median(times), 3),
                    "total_s": round(sum(times), 1),
                    "sources": dict(sources),
                    "failures": failures[:10],
                }
            )
        )


if __name__ == "__main__":
    main()
