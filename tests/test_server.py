"""The MCP server offers the same steps as read-only tools, inside one folder."""

import asyncio
import io
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from unittest.mock import patch

from pdfscope import server as server_module
from pdfscope.server import build_server
from support import EXAMPLE


class ServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        (base / "shared").mkdir()
        shutil.copy(EXAMPLE, base / "shared" / "northwind.pdf")
        shutil.copy(EXAMPLE, base / "private.pdf")
        self.shared = base / "shared"
        self.server = build_server(self.shared)

    async def call(self, tool, **arguments):
        result = await self.server.call_tool(tool, arguments)
        return result.content[0]

    async def test_tools_are_read_only_and_described(self):
        tools = {tool.name: tool for tool in await self.server.list_tools()}
        self.assertEqual(set(tools), {"pdfs", "outline", "find", "read", "table", "figure", "show"})
        for tool in tools.values():
            self.assertTrue(tool.annotations.read_only_hint, tool.name)
            self.assertTrue(tool.description, tool.name)

    async def test_steps_run_inside_the_folder(self):
        self.assertIn("northwind.pdf", (await self.call("pdfs")).text)
        self.assertIn("A year of steady growth", (await self.call("outline", file="northwind.pdf")).text)
        self.assertIn("Net income", (await self.call("find", file="northwind.pdf", query="167")).text)
        image = await self.call("show", file="northwind.pdf", ref="p2")
        self.assertEqual(image.type, "image")

    async def test_files_outside_the_folder_are_refused(self):
        with self.assertRaisesRegex(ToolError, "outside the folder"):
            await self.server.call_tool("outline", {"file": "../private.pdf"})

    async def test_a_document_in_use_is_not_closed_under_a_call(self):
        from pdfscope.document import Document

        shutil.copy(EXAMPLE, self.shared / "other.pdf")
        started, release = threading.Event(), threading.Event()
        page = Document.page

        def slow(doc, number):
            if doc.path.endswith("northwind.pdf") and number == 2:
                started.set()
                release.wait(10)
            return page(doc, number)

        with (
            patch.object(server_module, "MAX_DOCS", 1),
            patch.object(Document, "page", slow),
            patch.dict(os.environ, {"PDFSCOPE_NO_CACHE": "1"}),
        ):
            first = asyncio.create_task(self.server.call_tool("outline", {"file": "northwind.pdf"}))
            self.assertTrue(await asyncio.to_thread(started.wait, 10))
            await self.server.call_tool("outline", {"file": "other.pdf"})  # with room for 1, northwind.pdf is let go
            release.set()
            result = await first
        self.assertIn("A year of steady growth", result.content[0].text)

    async def test_show_checks_the_page_and_keeps_images_small(self):
        from PIL import Image

        with self.assertRaisesRegex(ToolError, "outside 1 to 3"):
            await self.server.call_tool("show", {"file": "northwind.pdf", "ref": "p9"})
        Image.effect_noise((4000, 3000), 80).convert("RGB").save(self.shared / "photo.jpg", quality=95)
        image = await self.call("show", file="photo.jpg", ref="p1")
        import base64

        data = base64.b64decode(image.data)
        self.assertLess(len(data), 5_000_000)  # Claude's limit for one image
        self.assertLessEqual(max(Image.open(io.BytesIO(data)).size), 1600)

    async def test_the_file_list_says_when_it_is_cut_short(self):
        many = self.shared / "many"
        many.mkdir()
        for k in range(205):
            (many / f"f{k:03}.pdf").write_bytes(b"%PDF-1.4")
        text = (await self.call("pdfs")).text
        self.assertIn("first 200", text)

    async def test_long_reads_stop_within_the_time_budget(self):
        with patch.object(server_module, "BUDGET", 0), patch.dict("os.environ", {"PDFSCOPE_NO_CACHE": "1"}):
            text = (await self.call("outline", file="northwind.pdf")).text
        self.assertIn("call outline again", text)


if __name__ == "__main__":
    unittest.main()
