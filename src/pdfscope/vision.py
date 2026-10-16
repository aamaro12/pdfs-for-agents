"""OCR with Apple Vision's document reader, through a small Swift helper compiled on first use.

The helper needs macOS 26 or later and the Xcode command line tools (for `swiftc`). It stays running,
so Vision's models load once per process rather than once per page.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from importlib import resources
from pathlib import Path

CACHE = Path(os.environ.get("PDFSCOPE_CACHE", Path.home() / ".cache" / "pdfscope"))


class Unavailable(RuntimeError):
    pass


def available() -> str | None:
    """None if Vision OCR can run here, otherwise the reason it cannot."""
    if platform.system() != "Darwin":
        return "Apple Vision OCR needs macOS"
    major = int(platform.mac_ver()[0].split(".")[0] or 0)
    if major < 26:
        return "Apple Vision's document reader needs macOS 26 or later"
    if not shutil.which("swiftc") and not _binary().exists():
        return "compiling the OCR helper needs the Xcode command line tools (xcode-select --install)"
    return None


def _source() -> str:
    return resources.files("pdfscope").joinpath("vision.swift").read_text()


def _binary() -> Path:
    digest = hashlib.sha256(_source().encode()).hexdigest()[:12]
    return CACHE / "bin" / f"vision-{digest}"


def _build() -> Path:
    try:
        return _compile()
    except OSError as e:  # a cache folder that cannot be written, a full disk: no OCR, but no crash
        raise Unavailable(f"could not build the OCR helper in {CACHE}: {e}") from None


def _compile() -> Path:
    binary = _binary()
    if binary.exists():
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        main = Path(tmp) / "main.swift"
        main.write_text(_source())
        out = Path(tmp) / "vision"
        done = subprocess.run(["swiftc", "-O", "-o", str(out), str(main)], capture_output=True, text=True)
        if done.returncode:
            raise Unavailable(f"could not compile the OCR helper: {done.stderr.strip()[:500]}")
        shutil.move(out, binary)
    return binary


class Vision:
    """A running OCR helper. Thread-safe; one page at a time."""

    def __init__(self, languages: str = "en-US"):
        reason = available()
        if reason:
            raise Unavailable(reason)
        self._env = dict(os.environ, PDFSCOPE_LANGUAGES=languages)
        self._lock = threading.Lock()
        self._start()

    def _start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                [str(_build())],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=self._env,
            )
        except OSError as e:
            raise Unavailable(f"could not start the OCR helper: {e}") from None

    def read(self, image_path: str) -> dict:
        """Lines, paragraphs, tables and lists found in an image, with boxes as page fractions. A helper that
        has stopped (killed, or crashed on a page) is started again once."""
        with self._lock:
            line = ""
            for attempt in range(2):
                if self._proc.poll() is not None:
                    self._start()
                try:
                    self._proc.stdin.write(image_path.replace("\n", " ") + "\n")
                    self._proc.stdin.flush()
                    line = self._proc.stdout.readline()
                except OSError:
                    line = ""
                if line:
                    break
                self._proc.kill()
                self._proc.wait()
        if not line:
            raise Unavailable("the OCR helper stopped")
        page = json.loads(line)
        if page.get("error"):
            raise Unavailable(f"Vision could not read the page: {page['error']}")
        return page

    def read_image(self, image) -> dict:
        """Same as read(), for a PIL image."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            image.save(path)
            return self.read(path)
        finally:
            os.unlink(path)

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.stdin.close()
            self._proc.wait(timeout=10)

    def __enter__(self) -> Vision:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
