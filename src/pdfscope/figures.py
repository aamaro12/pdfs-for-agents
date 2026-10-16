"""Reading figures with small vision models on the Mac (MLX, Apple Silicon).

Florence-2 base describes any image in under a second. PaddleOCR-VL 1.6 turns a chart into a table in about
2 s: on 60 ChartQA test charts it put 90.8% of the values in the right row (within 5%), with its language
layers at 8 bits and the chart scaled to 600 px wide, which was as accurate as larger sizes and faster.
Both load on first use and stay in memory. Install with `pip install "pdfscope[figures]"`.
"""

from __future__ import annotations

import os
import platform
import re
import tempfile
import threading

DESCRIBER = "mlx-community/Florence-2-base-ft-8bit"
CHART_READER = "PaddlePaddle/PaddleOCR-VL-1.6"
CHART_WIDTH = 600
CHARTISH = re.compile(
    r"\b(chart|graph|plot|histogram|bar|bars|pie|axis|axes|trend line|line graph|diagram of values)\b", re.I
)

_models: dict[str, tuple] = {}
_lock = threading.Lock()


class Unavailable(RuntimeError):
    pass


def available() -> str | None:
    """None if figures can be read here, otherwise the reason they cannot."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return "reading figures needs a Mac with Apple Silicon"
    try:
        import mlx_vlm  # noqa: F401
    except ImportError:
        return 'reading figures needs the figures extra: pip install "pdfscope[figures] @ git+https://github.com/aamaro12/pdfs-for-agents"'
    return None


def _model(repo: str, quantize: bool = False):
    with _lock:
        if repo not in _models:
            reason = available()
            if reason:
                raise Unavailable(reason)
            from mlx_vlm import load
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config

            model, processor = load(repo, trust_remote_code=True)
            if quantize:  # 8-bit language layers: twice as fast to write, no loss on charts
                import mlx.nn as nn

                nn.quantize(
                    model,
                    group_size=64,
                    bits=8,
                    class_predicate=lambda path, m: (
                        isinstance(m, nn.Linear) and not path.startswith("visual") and m.weight.shape[-1] % 64 == 0
                    ),
                )
            config = load_config(repo, trust_remote_code=True)
            _models[repo] = (model, processor, config, apply_chat_template)
        return _models[repo]


def _run(repo: str, prompt: str, image, max_tokens: int, quantize: bool = False) -> str:
    model, processor, config, template = _model(repo, quantize)  # raises Unavailable first
    from mlx_vlm import generate

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        image.save(path)
        with _lock:  # one generation at a time on the GPU
            out = generate(
                model,
                processor,
                template(processor, config, prompt, num_images=1),
                image=[path],
                max_tokens=max_tokens,
                temperature=0.0,
            )
        return out.text
    finally:
        os.unlink(path)


def describe(image) -> str:
    """One to three sentences about an image (Florence-2 base)."""
    image = image.convert("RGB")
    image.thumbnail((768, 768))
    text = re.sub(r"</?s>|<pad>|<[A-Z_]+>", "", _run(DESCRIBER, "<MORE_DETAILED_CAPTION>", image, 80)).strip()
    sentences, seen = [], set()
    for s in re.split(r"(?<=[.!?])\s+", text):
        s = s.strip()
        if s and s.lower() not in seen and len(sentences) < 3:
            seen.add(s.lower())
            sentences.append(s)
    return " ".join(sentences)


def looks_like_chart(description: str, labels: list[str]) -> bool:
    """A chart by its description, or by its labels: mostly figures, as on an axis or above bars."""
    if CHARTISH.search(description or ""):
        return True
    numeric = [t for t in labels if re.fullmatch(r"[\s$€£(−-]*\d[\d,.]*\s*[%)kmbn]*", t.strip(), re.I)]
    return len(labels) >= 6 and len(numeric) >= 0.5 * len(labels)


def chart_table(image) -> list[list[str]]:
    """The data behind a chart as rows of cells (PaddleOCR-VL 1.6)."""
    image = image.convert("RGB")
    if image.width > CHART_WIDTH:
        image = image.resize((CHART_WIDTH, round(image.height * CHART_WIDTH / image.width)))
    text = _run(CHART_READER, "Chart Recognition:", image, 1200, quantize=True)
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and not all(set(c) <= set("-: ") for c in cells):
            rows.append(cells)
    if len(rows) >= 2 and len(rows[0]) == len(rows[1]) - 1:
        rows[0] = [""] + rows[0]  # a header row written without the empty corner cell
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]
