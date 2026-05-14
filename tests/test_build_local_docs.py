import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_local_docs  # noqa: E402


class BuildLocalDocsTest(unittest.TestCase):
    def test_render_markdown_handles_common_doc_blocks(self):
        body, headings = build_local_docs.render_markdown(
            "# build_overview Local Docs\n\n"
            "See [developer guide](developer-guide.md) and `state root`.\n\n"
            "| Name | Meaning |\n"
            "| --- | --- |\n"
            "| `repo` | **source** |\n\n"
            "```bash\n"
            "python3 scripts/build_local_docs.py\n"
            "```\n"
        )

        self.assertEqual("build_overview Local Docs", headings[0].title)
        self.assertIn('href="developer-guide.html"', body)
        self.assertIn("<table>", body)
        self.assertIn("<strong>source</strong>", body)
        self.assertIn("python3 scripts/build_local_docs.py", body)

    def test_build_outputs_include_one_html_page_per_markdown_doc(self):
        docs = build_local_docs.load_documents()
        outputs = build_local_docs.build_outputs(docs)
        reference_pages = [
            path
            for path in outputs
            if path.parent == build_local_docs.REFERENCE_DIR and path.suffix == ".html"
        ]

        self.assertEqual(len(list(build_local_docs.DOCS_DIR.glob("*.md"))), len(reference_pages))
        self.assertIn(build_local_docs.INDEX_PATH, outputs)
        self.assertIn(build_local_docs.REFERENCE_DIR / "developer-guide.html", outputs)
        self.assertIn(build_local_docs.PUBLIC_INDEX_PATH, outputs)
        self.assertIn(build_local_docs.PUBLIC_REFERENCE_DIR / "developer-guide.html", outputs)
        self.assertIn(build_local_docs.PUBLIC_VISUAL_GUIDE_PATH, outputs)
        developer_page = outputs[build_local_docs.REFERENCE_DIR / "developer-guide.html"]
        self.assertIn('max-height: calc(100vh - 112px);', developer_page)
        self.assertIn('activeLink.scrollIntoView', developer_page)
        self.assertIn('aria-current', developer_page)
        self.assertIn('window.setTimeout(setActive, 160)', developer_page)
        public_index_page = outputs[build_local_docs.PUBLIC_INDEX_PATH]
        self.assertIn("OpenRelix 开发文档", public_index_page)
        self.assertIn('href="../openrelix-icon.png"', public_index_page)
        public_visual_page = outputs[build_local_docs.PUBLIC_VISUAL_GUIDE_PATH]
        self.assertIn('href="../index.html"', public_visual_page)
        self.assertIn("https://github.com/openrelix/openrelix/blob/main/scripts/build_overview.py", public_visual_page)
        self.assertNotIn("../docs/openrelix-icon.png", public_visual_page)

    def test_language_marker_wins_over_body_character_mix(self):
        markdown = (
            "# English Doc\n\n"
            "> Languages: English | [简体中文](doc.zh-CN.md)\n\n"
            "This English document may mention 中文商标 or local Chinese filing details.\n"
        )

        self.assertEqual("en", build_local_docs.infer_language(markdown, Path("doc.md")))


if __name__ == "__main__":
    unittest.main()
