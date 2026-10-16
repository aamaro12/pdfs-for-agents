# PDFs for agents

`pdfscope` lets an AI agent read a PDF the way an analyst does: outline first, then search, then the page or table it needs. Every block comes with its page and its box on the page, so an answer can cite `[report.pdf] p150:b2` and a reviewer can check it.

Two things make PDFs hard for agents:
- **The text layer can lie.** In NVIDIA's 2024 annual review, 156 of 187 pages use fonts whose characters map to the wrong letters: the page shows "Our worldwide business activities" and the text layer says `7WT YQTNFYKFG DWUKPGUU CEVKXKVKGU`.
- **Reading pages as images with a vision model is slow on a laptop.** The small OCR models measured here took 5 to 72 s a page on an M2 Mac.

`pdfscope` reads the text layer when it can be trusted and uses Apple Vision, on the Mac itself, when it cannot. It then rebuilds what an agent needs: tables as grids, columns in reading order, and running headers kept apart from the content. Most pages take a few hundredths of a second. A page that needs OCR takes about 0.7 s for a typeset report page and about 2 s for a dense scan.

| Step | What it returns |
|---|---|
| `outline` | Every page: how it was read (text layer or OCR), its headings and tables, and warnings such as a scrambled text layer |
| `find` | Blocks, table cells and the labels inside charts that contain some text, or figures that round to a number. `60.9bn` finds 60,922 in a table in millions, with its row and column |
| `read` | Pages in reading order, every block tagged with its citation, tables as grids |
| `table` | One table with its figures parsed (brackets are negative) and the units stated above it |
| `figure` | What a figure shows: a short description of any image, and for a chart its data as a table, checked against the numbers printed on it |
| `show` | An image of a page or a block, to look at a figure or check a citation by eye |

## Results

[olmOCR-bench](https://huggingface.co/datasets/allenai/olmOCR-bench) checks facts about the Markdown a tool produces: that a table cell sits next to the right neighbours, that two passages come in the right order, that a running header is left out. These are its 845 PDFs outside the two maths categories, scored with the benchmark's own scorer on an M2 Mac with 16 GB.

| Tool | Overall | Tables | Headers and footers | Multi-column | Tiny text | Old scans | Time a page |
|---|---|---|---|---|---|---|---|
| The PDF text layer alone | 39.8 | 0.1 | 39.3 | 55.4 | 49.1 | 13.3 | 0.01 s |
| Apple Vision lines alone | 32.2 | 0.2 | 27.1 | 12.7 | 34.6 | 18.4 | 0.37 s median |
| [foliodoc](https://github.com/Meet2147/foliodoc) 0.1.0 | 59.3 | 53.8 | 59.7 | 62.9 | 57.9 | 21.7 | 0.21 s mean |
| **pdfscope 0.3.0** | **65.1** | **54.9** | **77.9** | **67.5** | **70.6** | 20.5 | 0.51 s mean, 0.04 s median |

Scores are percentages of tests passed, averaged over the categories (and a check that the output is not garbage, where every tool scores 99 or more). Each overall score has a margin of about ±1.3.

For context, slower models on the same Mac:
- **Granite-Docling 258M**, a small vision model that reads whole pages, scored 64.5 on a 150-PDF sample of the same categories at a median of 12 s a page. On that sample `pdfscope` scores 66.0 and foliodoc 58.7.
- **LightOnOCR-3 0.8B** reports 85.5 on the whole benchmark, run on a GPU. On the M2 it took 24 to 46 s a page.

So `pdfscope` matches a small vision model at a twentieth of the time, stays well below GPU models, and spends its effort on what an agent needs: citations, search and tables it can query.

## How it reads a page

1. **Text layer first.** pdfium reads each character with its box. The text layer is set aside when pdfium reports that its font has no usable map to Unicode, when it is full of control characters, or when its words do not read like words.
2. **OCR where needed.** On macOS 26 or later, Apple Vision reads the page on the device: its document reader for tables and paragraphs, its line reader for text. Large images with no text drawn over them, such as a table pasted as a picture, are read the same way.
3. **Layout.**
   - **Running headers, footers and page numbers** are found by repetition across nearby pages, or for a single page by their place at the edge.
   - **Tables** come from ruling lines when the PDF draws them, otherwise from columns of figures: columns are the gutters between words, and column headings such as "Jan 28, 2024 Jan 29, 2023" are split by date and placed over their figures. Rules drawn only under columns of figures, as statements draw subtotal lines, are read as underlines, not as a grid.
   - **Columns of text** are found from a vertical gutter and read left before right, with titles and full-width blocks around them.
   - **Figures** are embedded images and drawn charts: clusters of bars, plotted lines and markers with the axes around them. A chart's labels go with the chart rather than into the text, and the caption next to it becomes its title.
4. **Cache.** Each page is read once; later calls on the same file come from a cache in `~/.cache/pdfscope`. A new version of pdfscope reads pages afresh, and a page whose OCR failed is not cached.

## Install

Python 3.11 or later.

```
git clone https://github.com/aamaro12/pdfs-for-agents
cd pdfs-for-agents
pip install -e ".[mcp]"
```

`pdfscope` reads PDFs and images (PNG, JPEG, TIFF and others; HEIC with `pip install pillow-heif`). An image is read as a document of one page, or one page per frame for a multi-frame TIFF or GIF.

OCR needs macOS 26 or later and the Xcode command line tools (`xcode-select --install`). The first page `pdfscope` OCRs compiles a small Swift helper, and the first run of that helper loads Vision's models, which takes about 45 s once. Without OCR, everything else works on any system, from the text layer.

## Figures

`pdfscope figure FILE p3:b4` reads a figure with two small vision models that run on the Mac (Apple Silicon, through MLX):

| Job | Model | Measured on this M2 |
|---|---|---|
| Describe any image | [Florence-2 base](https://huggingface.co/microsoft/Florence-2-base-ft) (0.23B, MIT) | 0.6 s an image; specific on 13 of 15 varied images (photos, maps, diagrams, logos, an infographic) |
| Turn a chart into a table | [PaddleOCR-VL 1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) (0.9B, Apache-2.0), language layers at 8 bits, chart at 600 px wide | 1.6 s a chart; 90.8% of values in the right row (within 5%) on 60 ChartQA test charts: 96% on generated charts, 84% on charts taken from the web |

Every figure gets a description. When the description or the labels say chart, the chart reader runs too, and its figures are checked against the numbers printed on the chart (axis ticks and years aside): any that are not printed there are listed to check. Smaller chart models were faster but wrong (UniChart, DePlot, SmolVLM, LFM2-VL), and LightOnOCR-3 was as accurate on charts from the web at 7 times the time.

```
pip install -e ".[mcp,figures]"
```

The models download on first use (about 2.2 GB) and stay in memory. Results are cached like pages.

## Try it

`examples/northwind.pdf` is three pages of a made-up annual report: a page of text in two columns, an income statement, and a bar chart.

```
$ pdfscope outline examples/northwind.pdf
**northwind.pdf** · sha256 b9123fea54f3 · 3 pages · read from text layer: 3
Cite blocks like [northwind.pdf] p1:b2.

| Page | Read from | Headings, or how the page starts | Tables | Figures |
|---|---|---|---|---|
| p1 | text layer | A year of steady growth (p1:b2) |  |  |
| p2 | text layer | Northwind Trading Consolidated Statement of Income (p2:b2) | p2:b4 13×3: Dec 31, 2026, Dec 31, 2025 |  |
| p3 | text layer | Revenue by region (p3:b2) |  | p3:b4: Figure 1: Revenue by region, 2026 and 20 |
```

Find a figure as a report would quote it, even when the table is in thousands:

```
$ pdfscope find examples/northwind.pdf 1.3m
| Cite | Kind | Text | Row | Column |
|---|---|---|---|---|
| p2:b4 | table cell | 1,320 | Revenue | Dec 31, 2026 |
```

Read the table, with the units stated above it:

```
$ pdfscope table examples/northwind.pdf p2:b4
Table p2:b4 · figures in thousands

|  | Dec 31, 2026 | Dec 31, 2025 |
|---|---|---|
| Revenue | 1,320 | 1,200 |
| Cost of revenue | 766 | 696 |
| Gross profit | 554 | 504 |
...
```

Read a page, every block tagged with its citation. The two columns come out in order, and the running header and page number are marked rather than mixed into the text:

```
$ pdfscope read examples/northwind.pdf 1
## p1 · read from text layer

*Header:* Northwind Trading · Annual Report 2026 [p1:b1]

### A year of steady growth [p1:b2]

Northwind Trading sells specialty foods to restaurants and grocers in twelve countries. Revenue grew 10% in fiscal 2026 ... [p1:b3]

Operating costs rose more slowly than revenue, so operating income increased to $235 thousand. ... [p1:b4]

*Footer:* 1 [p1:b5]
```

Read the chart (with the `figures` extra):

```
$ pdfscope figure examples/northwind.pdf p3:b4
Figure p3:b4: Figure 1: Revenue by region, 2026 and 2025 (thousands of dollars)

Looks like: A bar graph is displayed on a white background. ...

Data read from the chart:

|  | 2026 | 2025 |
|---|---|---|
| North | 310 | 280 |
| South | 285 | 262 |
| East | 240 | 228 |
| West | 205 | 196 |
| Online | 280 | 234 |

Every figure in the table appears among the numbers printed on the chart (which series and column each belongs to is not checked).

Text inside the figure (the PDF's text, exact): 2026 · 400 · 2025 · 310 · 285 · ...
```

`pdfscope show FILE p2:b4 -o table.png` saves an image of the block. Add `--json` to any step for the full result, `--ocr always` or `--ocr never` to control OCR, and `--languages fr-FR,en-US` (or `auto`) for OCR in other languages.

## Use it from an agent

`pdfscope mcp --root FOLDER` serves the steps as read-only MCP tools, plus `pdfs` to list the files. The server reads PDFs under that folder and nowhere else. Its instructions tell the agent to start with `outline`, to search with the document's own words rather than the question, and to cite every figure by its block. On a long scanned PDF, `outline` and `find` stop after about 40 s of OCR and say which pages are left, so no call runs past the client's timeout; the next call carries on from there.

For Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.pdfscope]
command = "pdfscope"
args = ["mcp", "--root", "/path/to/pdfs"]
```

For clients configured in JSON:

```json
{"mcpServers": {"pdfscope": {"command": "pdfscope", "args": ["mcp", "--root", "/path/to/pdfs"]}}}
```

## Reproduce the benchmark

```
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download as d; d('allenai/olmOCR-bench', repo_type='dataset', local_dir='bench/data')"
python bench/run_olmocr.py --data bench/data/bench_data --name pdfscope
pip install "olmocr[bench]"   # in a separate environment
python -m olmocr.bench.benchmark --dir bench/data/bench_data --candidate pdfscope
```

`bench/run_olmocr.py` skips the maths categories by default; pass `--categories` to choose.

## Limits

- OCR runs only on macOS 26 or later. Elsewhere, a scanned page or a scrambled text layer is reported in `outline` and read as well as its text layer allows.
- Chart tables and image descriptions come from small models: about 1 value in 10 on a chart can be wrong or in the wrong series, which the printed-number check catches only when the chart prints its values. The figure step runs on Apple Silicon only, and on scanned pages figures are not found at all.
- Tables without ruling lines need columns of figures or short aligned cells. Tables of long text without ruling lines can come out as paragraphs.
- Old, noisy scans are hard for every fast tool here (about 20 on the benchmark); they need a GPU model.

## Related work

foliodoc takes the same fast path (text layer, then Apple Vision or RapidOCR) to convert documents to Markdown, and is the closest comparison above. olmOCR-bench is by the Allen Institute for AI.

## Licence

MIT
