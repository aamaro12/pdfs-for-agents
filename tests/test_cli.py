"""The command line runs each step and prints Markdown or JSON."""

import contextlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from pdfscope.cli import main
from support import EXAMPLE


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*args])
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_outline_and_find(self):
        code, out, _ = run("outline", str(EXAMPLE), "--ocr", "never")
        self.assertEqual(code, 0)
        self.assertIn("**northwind.pdf**", out)
        code, out, _ = run("find", str(EXAMPLE), "167", "--json", "--ocr", "never")
        self.assertEqual(json.loads(out)["hits"][0]["row"], "Net income")

    def test_errors_go_to_stderr(self):
        code, out, err = run("table", str(EXAMPLE), "p7:b1", "--ocr", "never")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("outside 1 to 3", err)

    def test_mcp_without_its_extra_says_how_to_install_it(self):
        with patch.dict(sys.modules, {"pdfscope.server": None}):
            code, out, err = run("mcp")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("git+https://github.com/aamaro12/pdfs-for-agents", err)

    def test_files_that_cannot_be_opened_get_a_message_not_a_traceback(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.pdf"
            empty.write_bytes(b"")
            for path, message in ((empty, "could not open"), (Path(tmp) / "missing.pdf", "no such file")):
                code, out, err = run("outline", str(path), "--ocr", "never")
                self.assertEqual((code, out), (1, ""), err)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
