import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"


def _doc_base(path: Path) -> str:
    name = path.name
    if name.endswith(".zh-CN.md"):
        return name[: -len(".zh-CN.md")]
    if name.endswith(".en.md"):
        return name[: -len(".en.md")]
    return path.stem


class DocsBilingualTest(unittest.TestCase):
    def test_markdown_docs_have_english_and_chinese_versions(self):
        groups = {}
        for path in DOCS_DIR.glob("*.md"):
            groups.setdefault(_doc_base(path), set()).add(path.name)

        missing = []
        for base, names in sorted(groups.items()):
            default_name = f"{base}.md"
            english_names = {default_name, f"{base}.en.md"}
            chinese_names = {default_name, f"{base}.zh-CN.md"}

            has_default = default_name in names
            has_english = bool(names & english_names)
            has_chinese = bool(names & chinese_names)
            has_pair = has_default and len(names) >= 2 and has_english and has_chinese
            if not has_pair:
                missing.append(f"{base}: {', '.join(sorted(names))}")

        self.assertEqual([], missing)

    def test_markdown_docs_link_to_language_companion_near_title(self):
        missing = []
        for path in sorted(DOCS_DIR.glob("*.md")):
            head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:8])
            if "Languages:" not in head and "语言版本：" not in head:
                missing.append(path.name)

        self.assertEqual([], missing)

    def test_html_docs_keep_inline_language_switching(self):
        missing = []
        for path in sorted(DOCS_DIR.glob("*.html")) + sorted((DOCS_DIR / "changelog").glob("*.html")):
            text = path.read_text(encoding="utf-8")
            has_controls = 'data-language-option="zh"' in text and 'data-language-option="en"' in text
            has_translations = any(marker in text for marker in ("data-en=", "data-en-html=", "textTranslations"))
            if not has_controls or not has_translations:
                missing.append(str(path.relative_to(REPO_ROOT)))

        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
