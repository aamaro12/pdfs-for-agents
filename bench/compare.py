"""Compare two candidates test by test on olmOCR-bench (run with the environment that has olmocr[bench]).

python bench/compare.py --data bench/data/bench_data --a pdfscope --b foliodoc --files multi_column,long_tiny_text
"""

import argparse
import collections
import json
import os

from olmocr.bench.tests import load_tests


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--files", default="multi_column,long_tiny_text")
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()
    lost, won = collections.Counter(), collections.Counter()
    examples = collections.defaultdict(list)
    for name in args.files.split(","):
        jsonl = os.path.join(args.data, ("table_tests" if name == "tables" else name) + ".jsonl")
        for test in load_tests(jsonl):
            base = test.pdf[:-4]
            res = {}
            for cand in (args.a, args.b):
                path = os.path.join(args.data, cand, f"{base}_pg{test.page}_repeat1.md")
                text = open(path, errors="replace").read() if os.path.exists(path) else ""
                res[cand] = test.run(text)
            a_ok, b_ok = res[args.a][0], res[args.b][0]
            if b_ok and not a_ok:
                lost[(name, test.type)] += 1
                examples[(name, test.type)].append((test.pdf, res[args.a][1][:160]))
            elif a_ok and not b_ok:
                won[(name, test.type)] += 1
    print(
        json.dumps(
            {
                "lost": {f"{k[0]}/{k[1]}": v for k, v in lost.items()},
                "won": {f"{k[0]}/{k[1]}": v for k, v in won.items()},
            },
            indent=1,
        )
    )
    for key, rows in examples.items():
        print(f"\n== {key[0]}/{key[1]}: lost {len(rows)}")
        pages = collections.Counter(p for p, _ in rows)
        print("   pages:", pages.most_common(8))
        for p, why in rows[: args.show]:
            print("  ", p.split("/")[-1][:28], "|", why)


if __name__ == "__main__":
    main()
