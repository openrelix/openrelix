#!/usr/bin/env python3
"""Build human-readable HTML pages from docs/*.md.

The Markdown files stay as the lightweight source of truth. This script creates
one HTML page per Markdown file for local browsing and onboarding, then mirrors
the same pages under docs/developer/ for static-site publishing.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
LOCAL_DOCS_DIR = REPO_ROOT / "local-docs"
REFERENCE_DIR = LOCAL_DOCS_DIR / "reference"
INDEX_PATH = LOCAL_DOCS_DIR / "index.html"
LOCAL_VISUAL_GUIDE_PATH = LOCAL_DOCS_DIR / "developer-guide.html"
PUBLIC_DOCS_DIR = DOCS_DIR / "developer"
PUBLIC_REFERENCE_DIR = PUBLIC_DOCS_DIR / "reference"
PUBLIC_INDEX_PATH = PUBLIC_DOCS_DIR / "index.html"
PUBLIC_VISUAL_GUIDE_PATH = PUBLIC_DOCS_DIR / "developer-guide.html"
GITHUB_SOURCE_ROOT = "https://github.com/openrelix/openrelix"


LANG_LABELS = {
    "zh-CN": ("中文", "Chinese"),
    "en": ("EN", "English"),
}


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    slug: str


@dataclass(frozen=True)
class Document:
    source: Path
    output_name: str
    key: str
    lang: str
    title: str
    body_html: str
    headings: tuple[Heading, ...]


def strip_language_suffix(name: str) -> str:
    if name.endswith(".zh-CN.md"):
        return name[: -len(".zh-CN.md")]
    if name.endswith(".en.md"):
        return name[: -len(".en.md")]
    if name.endswith(".md"):
        return name[: -len(".md")]
    return name


def output_name_for_source(path: Path) -> str:
    return path.name[: -len(".md")] + ".html"


def infer_language(markdown: str, path: Path) -> str:
    name = path.name
    if name.endswith(".zh-CN.md"):
        return "zh-CN"
    if name.endswith(".en.md"):
        return "en"
    head = "\n".join(markdown.splitlines()[:8])
    if re.search(r"^>\s*Languages:\s*English\b", head, re.MULTILINE):
        return "en"
    if re.search(r"^>\s*语言版本：", head, re.MULTILINE):
        return "zh-CN"
    sample = markdown[:4000]
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", sample))
    return "zh-CN" if chinese_chars >= 12 else "en"


def extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return _plain_text(match.group(1))
    return fallback


def _plain_text(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = value.replace("**", "").replace("*", "")
    return value.strip()


def _slugify(title: str, seen: dict[str, int]) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if not ascii_slug:
        ascii_slug = "section"
    count = seen.get(ascii_slug, 0)
    seen[ascii_slug] = count + 1
    if count:
        return f"{ascii_slug}-{count + 1}"
    return ascii_slug


def _split_url_anchor(url: str) -> tuple[str, str]:
    if "#" not in url:
        return url, ""
    base, anchor = url.split("#", 1)
    return base, f"#{anchor}"


def localize_markdown_href(url: str) -> str:
    url = url.strip()
    if not url or url.startswith("#"):
        return url
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return url
    base, anchor = _split_url_anchor(url)
    if base.endswith(".md"):
        return Path(base).name[: -len(".md")] + ".html" + anchor
    return url


def render_inline(text: str) -> str:
    tokens: dict[str, str] = {}

    def save(fragment: str) -> str:
        token = f"\u0000{len(tokens)}\u0000"
        tokens[token] = fragment
        return token

    def code_repl(match: re.Match[str]) -> str:
        return save(f"<code>{html.escape(match.group(1))}</code>")

    def autolink_repl(match: re.Match[str]) -> str:
        href = match.group(1)
        safe_href = html.escape(href, quote=True)
        return save(f'<a href="{safe_href}">{html.escape(href)}</a>')

    def link_repl(match: re.Match[str]) -> str:
        label = html.escape(match.group(1).strip())
        raw_href = match.group(2).strip()
        raw_href = raw_href.split(" ", 1)[0].strip()
        href = html.escape(localize_markdown_href(raw_href), quote=True)
        return save(f'<a href="{href}">{label}</a>')

    text = re.sub(r"`([^`]+)`", code_repl, text)
    text = re.sub(r"<(https?://[^>\s]+)>", autolink_repl, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, text)
    rendered = html.escape(text)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", rendered)
    for token, fragment in tokens.items():
        rendered = rendered.replace(token, fragment)
    return rendered


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.match(r"^:?-{3,}:?$", cell) for cell in cells)


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and "|" in lines[index + 1]
        and _is_table_separator(lines[index + 1])
    )


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_block_start(lines: list[str], index: int) -> bool:
    line = lines[index]
    return (
        bool(re.match(r"^#{1,6}\s+", line))
        or line.startswith("```")
        or line.startswith("> ")
        or bool(re.match(r"^\s*([-*_]\s*){3,}$", line))
        or bool(re.match(r"^\s*([-*+])\s+", line))
        or bool(re.match(r"^\s*\d+\.\s+", line))
        or _is_table_start(lines, index)
    )


def render_markdown(markdown: str) -> tuple[str, tuple[Heading, ...]]:
    lines = markdown.splitlines()
    html_parts: list[str] = []
    headings: list[Heading] = []
    seen_slugs: dict[str, int] = {}
    i = 0

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        if line.startswith("```"):
            language = line[3:].strip().split(" ", 1)[0]
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            class_attr = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            code = html.escape("\n".join(code_lines))
            html_parts.append(f"<pre><code{class_attr}>{code}</code></pre>")
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading_match:
            level = len(heading_match.group(1))
            title = _plain_text(heading_match.group(2))
            slug = _slugify(title, seen_slugs)
            headings.append(Heading(level=level, title=title, slug=slug))
            html_parts.append(
                f'<h{level} id="{slug}">{render_inline(heading_match.group(2))}</h{level}>'
            )
            i += 1
            continue

        if re.match(r"^\s*([-*_]\s*){3,}$", line):
            html_parts.append("<hr>")
            i += 1
            continue

        if _is_table_start(lines, i):
            header = _table_cells(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_table_cells(lines[i]))
                i += 1
            html_parts.append(_render_table(header, rows))
            continue

        if line.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:].strip())
                i += 1
            quote = " ".join(quote_lines)
            html_parts.append(f"<blockquote><p>{render_inline(quote)}</p></blockquote>")
            continue

        list_match = re.match(r"^\s*(([-*+])|(\d+\.))\s+(.+)$", line)
        if list_match:
            ordered = bool(list_match.group(3))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                item_match = re.match(r"^\s*(([-*+])|(\d+\.))\s+(.+)$", lines[i])
                if not item_match:
                    break
                same_kind = bool(item_match.group(3)) == ordered
                if not same_kind:
                    break
                item = item_match.group(4)
                i += 1
                continuation: list[str] = []
                while i < len(lines) and lines[i].startswith("  ") and not re.match(
                    r"^\s*(([-*+])|(\d+\.))\s+", lines[i]
                ):
                    if lines[i].strip():
                        continuation.append(lines[i].strip())
                    i += 1
                if continuation:
                    item = " ".join([item, *continuation])
                items.append(f"<li>{render_inline(item)}</li>")
            html_parts.append(f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>")
            continue

        paragraph_lines = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not _is_block_start(lines, i):
            paragraph_lines.append(lines[i].strip())
            i += 1
        paragraph = " ".join(paragraph_lines)
        html_parts.append(f"<p>{render_inline(paragraph)}</p>")

    return "\n".join(html_parts), tuple(headings)


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    head_cells = "".join(f"<th>{render_inline(cell)}</th>" for cell in header)
    body_rows = []
    for row in rows:
        padded = row + [""] * max(0, len(header) - len(row))
        cells = "".join(f"<td>{render_inline(cell)}</td>" for cell in padded[: len(header)])
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        '<div class="table-wrap"><table>'
        f"<thead><tr>{head_cells}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


def load_documents(docs_dir: Path = DOCS_DIR) -> list[Document]:
    documents: list[Document] = []
    for source in sorted(docs_dir.glob("*.md")):
        markdown = source.read_text(encoding="utf-8")
        body_html, headings = render_markdown(markdown)
        documents.append(
            Document(
                source=source,
                output_name=output_name_for_source(source),
                key=strip_language_suffix(source.name),
                lang=infer_language(markdown, source),
                title=extract_title(markdown, source.stem),
                body_html=body_html,
                headings=headings,
            )
        )
    return documents


def _document_sort_key(doc: Document) -> tuple[str, int, str]:
    lang_rank = 0 if doc.lang == "zh-CN" else 1
    return (doc.key, lang_rank, doc.output_name)


def _group_documents(documents: Iterable[Document]) -> dict[str, list[Document]]:
    groups: dict[str, list[Document]] = {}
    for doc in documents:
        groups.setdefault(doc.key, []).append(doc)
    for docs in groups.values():
        docs.sort(key=_document_sort_key)
    return dict(sorted(groups.items()))


def _language_links(doc: Document, variants: list[Document]) -> str:
    links = []
    for variant in variants:
        label = LANG_LABELS.get(variant.lang, (variant.lang, variant.lang))[0]
        active = " active" if variant.output_name == doc.output_name else ""
        href = html.escape(variant.output_name, quote=True)
        links.append(f'<a class="lang-link{active}" href="{href}">{html.escape(label)}</a>')
    return "\n".join(links)


def _toc_html(doc: Document) -> str:
    toc_headings = [heading for heading in doc.headings if 1 < heading.level <= 3]
    if not toc_headings:
        empty = "This document has no section headings." if doc.lang == "en" else "这份文档没有二级标题。"
        return f'<p class="toc-empty">{empty}</p>'
    links = []
    for heading in toc_headings:
        class_name = "toc-link sub" if heading.level == 3 else "toc-link"
        links.append(
            f'<a class="{class_name}" href="#{html.escape(heading.slug, quote=True)}">'
            f"{html.escape(heading.title)}</a>"
        )
    return "\n".join(links)


DOC_PAGE_CSS = """
:root {
  color-scheme: light;
  --ink: #181a1f;
  --muted: #626873;
  --paper: #ffffff;
  --paper-soft: #f7f8f5;
  --line: #dedfd9;
  --line-strong: #c9cbc4;
  --accent: #0f766e;
  --accent-soft: #e6f4f1;
  --code-bg: #101827;
  --code-fg: #f8fafc;
  --radius: 8px;
  --max: 1180px;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--paper-soft); }
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper-soft);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  line-height: 1.68;
}
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 3px; }
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 0.94em;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 1px solid rgba(222, 223, 217, 0.9);
  background: rgba(255,255,255,0.94);
  backdrop-filter: blur(16px);
}
.topbar-inner {
  width: min(var(--max), calc(100% - 32px));
  min-height: 64px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 18px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  font-weight: 760;
  text-decoration: none;
  white-space: nowrap;
}
.brand img { width: 30px; height: 30px; border-radius: 7px; }
.topnav {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  overflow-x: auto;
  scrollbar-width: none;
}
.topnav::-webkit-scrollbar { display: none; }
.topnav a,
.lang-link {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--ink);
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
  background: #fff;
}
.topnav a:hover,
.lang-link:hover,
.lang-link.active {
  border-color: #bddbd5;
  background: var(--accent-soft);
  color: #0f5f59;
}
.shell {
  width: min(var(--max), calc(100% - 32px));
  margin: 34px auto 72px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 260px;
  gap: 28px;
  align-items: start;
}
.article {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--paper);
  padding: clamp(22px, 4vw, 46px);
}
.doc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  align-items: center;
  margin-bottom: 24px;
  color: var(--muted);
  font-size: 0.95rem;
}
.doc-meta code { color: var(--ink); background: #f0f1ed; padding: 2px 6px; border-radius: 5px; }
.language-switch { display: inline-flex; flex-wrap: wrap; gap: 8px; margin-left: auto; }
.content h1 {
  margin: 0 0 18px;
  font-size: clamp(2.1rem, 4vw, 3.2rem);
  line-height: 1.08;
  letter-spacing: 0;
}
.content h2 {
  margin: 46px 0 12px;
  padding-top: 6px;
  font-size: clamp(1.5rem, 3vw, 2rem);
  line-height: 1.18;
  letter-spacing: 0;
}
.content h3 {
  margin: 30px 0 8px;
  font-size: 1.24rem;
  line-height: 1.28;
}
.content h4 { margin: 24px 0 8px; font-size: 1.04rem; }
.content p,
.content li {
  color: var(--muted);
  font-size: 1.04rem;
}
.content p { margin: 12px 0; }
.content ul,
.content ol { padding-left: 1.35em; margin: 12px 0 18px; }
.content li + li { margin-top: 6px; }
.content blockquote {
  margin: 20px 0;
  padding: 14px 18px;
  border-left: 4px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 0 var(--radius) var(--radius) 0;
}
.content blockquote p { margin: 0; color: #33524d; }
.content pre {
  margin: 18px 0 24px;
  padding: 18px;
  overflow: auto;
  border-radius: var(--radius);
  background: var(--code-bg);
  color: var(--code-fg);
}
.content pre code { color: inherit; font-size: 0.96rem; }
.content :not(pre) > code {
  color: #0f5f59;
  background: #eef4f1;
  border: 1px solid #d9e7e2;
  padding: 1px 5px;
  border-radius: 5px;
}
.content hr { border: 0; border-top: 1px solid var(--line); margin: 28px 0; }
.table-wrap {
  width: 100%;
  margin: 18px 0 26px;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
}
table {
  width: 100%;
  border-collapse: collapse;
  min-width: 620px;
  background: #fff;
}
th, td {
  padding: 11px 13px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th {
  color: var(--ink);
  background: #f3f5f0;
  font-weight: 780;
}
td { color: var(--muted); }
tr:last-child td { border-bottom: 0; }
.toc {
  position: sticky;
  top: 86px;
  max-height: calc(100vh - 112px);
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--paper);
  padding: 18px;
  scrollbar-width: thin;
}
.toc-title {
  margin: 0 0 12px;
  color: var(--ink);
  font-size: 0.96rem;
  font-weight: 800;
}
.toc-link {
  display: block;
  border-left: 3px solid transparent;
  border-radius: 7px;
  padding: 7px 8px 7px 10px;
  color: var(--muted);
  text-decoration: none;
  font-weight: 650;
  line-height: 1.35;
}
.toc-link.sub { padding-left: 24px; font-size: 0.94rem; }
.toc-link.active,
.toc-link:hover {
  color: var(--accent);
  background: var(--accent-soft);
}
.toc-link.active {
  border-left-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
  font-weight: 800;
}
.toc-empty { color: var(--muted); margin: 0; }
.generated-note {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.92rem;
}
@media (max-width: 860px) {
  .topbar-inner { align-items: flex-start; flex-direction: column; padding: 12px 0; gap: 10px; }
  .topnav { width: 100%; margin-left: 0; }
  .shell { grid-template-columns: 1fr; margin-top: 18px; }
  .toc { position: static; order: -1; }
  .language-switch { margin-left: 0; }
}
"""


def render_document_page(doc: Document, variants: list[Document]) -> str:
    title = html.escape(doc.title)
    source_name = html.escape(f"docs/{doc.source.name}")
    language_links = _language_links(doc, variants)
    toc = _toc_html(doc)
    page_lang = "zh-CN" if doc.lang == "zh-CN" else "en"
    labels = {
        "home": "Docs home",
        "onboarding": "Visual guide",
        "developer": "Detailed guide",
        "generated": "Generated from Markdown",
        "source": "Source",
        "toc": "On this page",
        "update": "After editing Markdown:",
        "watch": "Auto-sync while editing:",
    } if doc.lang == "en" else {
        "home": "文档首页",
        "onboarding": "图解指南",
        "developer": "开发者详细指南",
        "generated": "HTML 由 Markdown 生成",
        "source": "源文件",
        "toc": "本文导览",
        "update": "更新源 Markdown 后运行：",
        "watch": "持续自动同步：",
    }
    return f"""<!doctype html>
<html lang="{page_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | OpenRelix Local Docs</title>
  <link rel="icon" href="../../docs/openrelix-icon.png">
  <style>{DOC_PAGE_CSS}</style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="../index.html">
        <img src="../../docs/openrelix-icon.png" alt="">
        <span>OpenRelix</span>
      </a>
      <nav class="topnav" aria-label="Local docs navigation">
        <a href="../index.html">{labels["home"]}</a>
        <a href="../developer-guide.html">{labels["onboarding"]}</a>
        <a href="developer-guide.html">{labels["developer"]}</a>
      </nav>
    </div>
  </header>
  <main class="shell">
    <article class="article">
      <div class="doc-meta">
        <span>{labels["generated"]}</span>
        <span>{labels["source"]} <code>{source_name}</code></span>
        <span class="language-switch" aria-label="Language versions">
          {language_links}
        </span>
      </div>
      <div class="content">
{doc.body_html}
      </div>
    </article>
    <aside class="toc" aria-label="Table of contents">
      <p class="toc-title">{labels["toc"]}</p>
      {toc}
      <p class="generated-note">{labels["update"]}<br><code>python3 scripts/build_local_docs.py</code><br>{labels["watch"]}<br><code>python3 scripts/build_local_docs.py --watch</code></p>
    </aside>
  </main>
  <script>
    (() => {{
      const toc = document.querySelector(".toc");
      const links = Array.from(document.querySelectorAll(".toc-link"));
      const headings = links
        .map((link) => document.getElementById(decodeURIComponent(link.hash.slice(1))))
        .filter(Boolean);
      if (!links.length || !headings.length) return;
      let lastActiveId = "";
      const applyActive = (activeId) => {{
        let activeLink = null;
        links.forEach((link) => {{
          const isActive = link.hash === "#" + activeId;
          link.classList.toggle("active", isActive);
          link.setAttribute("aria-current", isActive ? "true" : "false");
          if (isActive) activeLink = link;
        }});
        if (activeLink && toc && activeId !== lastActiveId) {{
          activeLink.scrollIntoView({{ block: "nearest", inline: "nearest" }});
          lastActiveId = activeId;
        }}
      }};
      const getScrollActiveId = () => {{
        const marker = Math.min(window.innerHeight * 0.42, 260);
        let activeId = headings[0].id;
        for (const heading of headings) {{
          if (heading.getBoundingClientRect().top <= marker) activeId = heading.id;
          else break;
        }}
        return activeId;
      }};
      const setActive = () => applyActive(getScrollActiveId());
      const setHashActive = () => {{
        const hashId = decodeURIComponent(window.location.hash.slice(1));
        if (hashId && headings.some((heading) => heading.id === hashId)) {{
          applyActive(hashId);
        }}
        requestAnimationFrame(setActive);
        window.setTimeout(setActive, 160);
        window.setTimeout(setActive, 520);
      }};
      window.addEventListener("scroll", setActive, {{ passive: true }});
      window.addEventListener("resize", setActive);
      window.addEventListener("hashchange", setHashActive);
      setHashActive();
    }})();
  </script>
</body>
</html>
"""


INDEX_CSS = """
:root {
  color-scheme: light;
  --ink: #181a1f;
  --muted: #626873;
  --paper: #ffffff;
  --paper-soft: #f7f8f5;
  --line: #dedfd9;
  --accent: #0f766e;
  --accent-soft: #e6f4f1;
  --radius: 8px;
  --max: 1180px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--paper-soft);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
  line-height: 1.6;
}
a { color: inherit; text-decoration: none; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  border-bottom: 1px solid rgba(222, 223, 217, 0.9);
  background: rgba(255,255,255,0.94);
  backdrop-filter: blur(16px);
}
.topbar-inner {
  width: min(var(--max), calc(100% - 32px));
  min-height: 64px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 18px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 760;
}
.brand img { width: 30px; height: 30px; border-radius: 7px; }
.topnav {
  display: flex;
  gap: 8px;
  margin-left: auto;
  overflow-x: auto;
  scrollbar-width: none;
}
.topnav::-webkit-scrollbar { display: none; }
.topnav a,
.doc-link {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: #fff;
  font-weight: 740;
}
.topnav a.active,
.topnav a:hover,
.doc-link:hover {
  border-color: #bddbd5;
  background: var(--accent-soft);
  color: #0f5f59;
}
.hero {
  width: min(var(--max), calc(100% - 32px));
  margin: 54px auto 30px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
  gap: 40px;
  align-items: end;
}
.kicker {
  color: var(--accent);
  font-weight: 820;
  margin: 0 0 12px;
}
h1 {
  margin: 0;
  font-size: clamp(2.2rem, 5vw, 3.9rem);
  line-height: 1.05;
  letter-spacing: 0;
}
.hero-copy {
  margin: 0;
  color: var(--muted);
  font-size: clamp(1.08rem, 2vw, 1.32rem);
}
.guide-card {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--paper);
  padding: 24px;
}
.guide-card h2 { margin: 0 0 8px; font-size: 1.28rem; }
.guide-card p { margin: 0 0 18px; color: var(--muted); }
.primary-link {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  padding: 0 18px;
  border-radius: 999px;
  background: #101827;
  color: #fff;
  font-weight: 800;
}
.docs {
  width: min(var(--max), calc(100% - 32px));
  margin: 0 auto 72px;
}
.docs-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 18px;
  margin: 36px 0 18px;
}
.docs-head h2 { margin: 0; font-size: clamp(1.7rem, 3vw, 2.3rem); }
.docs-head p { margin: 0; color: var(--muted); }
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.doc-card {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--paper);
  padding: 18px;
}
.doc-card h3 {
  margin: 0 0 8px;
  font-size: 1.08rem;
  line-height: 1.28;
}
.doc-source {
  margin: 0 0 14px;
  color: var(--muted);
  font-size: 0.94rem;
}
.doc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.note {
  margin-top: 24px;
  color: var(--muted);
  font-size: 0.96rem;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  background: #eef4f1;
  border: 1px solid #d9e7e2;
  padding: 1px 5px;
  border-radius: 5px;
}
@media (max-width: 860px) {
  .topbar-inner { align-items: flex-start; flex-direction: column; padding: 12px 0; gap: 10px; }
  .topnav { width: 100%; margin-left: 0; }
  .hero { grid-template-columns: 1fr; margin-top: 30px; }
  .grid { grid-template-columns: 1fr; }
}
"""


def render_index_page(documents: list[Document]) -> str:
    groups = _group_documents(documents)
    cards = []
    for key, variants in groups.items():
        zh = next((doc for doc in variants if doc.lang == "zh-CN"), None)
        en = next((doc for doc in variants if doc.lang == "en"), None)
        primary = zh or variants[0]
        actions = []
        for variant in variants:
            label = LANG_LABELS.get(variant.lang, (variant.lang, variant.lang))[0]
            href = html.escape(f"reference/{variant.output_name}", quote=True)
            actions.append(f'<a class="doc-link" href="{href}">{html.escape(label)}</a>')
        source_names = " / ".join(html.escape(doc.source.name) for doc in variants)
        title = html.escape(primary.title)
        cards.append(
            f"""<article class="doc-card">
  <h3>{title}</h3>
  <p class="doc-source">源文件：<code>{source_names}</code></p>
  <div class="doc-actions">{''.join(actions)}</div>
</article>"""
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OpenRelix 本地文档</title>
  <link rel="icon" href="../docs/openrelix-icon.png">
  <style>{INDEX_CSS}</style>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="index.html">
        <img src="../docs/openrelix-icon.png" alt="">
        <span>OpenRelix</span>
      </a>
      <nav class="topnav" aria-label="Local docs navigation">
        <a class="active" href="index.html">文档首页</a>
        <a href="developer-guide.html">图解指南</a>
        <a href="reference/developer-guide.html">开发者详细指南</a>
      </nav>
    </div>
  </header>
  <main>
    <section class="hero">
      <div>
        <p class="kicker">资料入口 / Local Docs</p>
        <h1>每份 Markdown，都有一份本地 HTML。</h1>
      </div>
      <div class="guide-card">
        <h2>先看开发者详细指南</h2>
        <p>完整开发路径、仓库地图、验证和排障都合在这一份；需要图解时再看图解指南。</p>
        <a class="primary-link" href="reference/developer-guide.html">打开开发者详细指南</a>
      </div>
    </section>
    <section class="docs">
      <div class="docs-head">
        <div>
          <h2>文档列表</h2>
          <p>中文和英文按同一主题放在一起；点击后进入对应 HTML 页面。</p>
        </div>
      </div>
      <div class="grid">
        {''.join(cards)}
      </div>
      <p class="note">Markdown 仍是源文件。更新后运行 <code>python3 scripts/build_local_docs.py</code> 重新生成本地 HTML；边写边自动同步可运行 <code>python3 scripts/build_local_docs.py --watch</code>；检查是否同步可运行 <code>python3 scripts/build_local_docs.py --check</code>。</p>
    </section>
  </main>
</body>
</html>
"""


def build_outputs(documents: list[Document]) -> dict[Path, str]:
    groups = _group_documents(documents)
    outputs: dict[Path, str] = {INDEX_PATH: render_index_page(documents)}
    for doc in documents:
        variants = groups[doc.key]
        outputs[REFERENCE_DIR / doc.output_name] = render_document_page(doc, variants)
    outputs.update(build_public_outputs(outputs))
    return outputs


def _public_path_for_local(path: Path) -> Path:
    if path == INDEX_PATH:
        return PUBLIC_INDEX_PATH
    if path.parent == REFERENCE_DIR:
        return PUBLIC_REFERENCE_DIR / path.name
    raise ValueError(f"unsupported local docs path: {path}")


def _with_external_target(href: str) -> str:
    return f'href="{href}" target="_blank" rel="noopener noreferrer"'


def publicize_generated_html(path: Path, content: str) -> str:
    if path == INDEX_PATH:
        return (
            content.replace("../docs/openrelix-icon.png", "../openrelix-icon.png")
            .replace("OpenRelix 本地文档", "OpenRelix 开发文档")
            .replace("Local Docs", "Developer Docs")
            .replace("每份 Markdown，都有一份本地 HTML。", "每份 Markdown，都有一份 HTML。")
            .replace("重新生成本地 HTML", "重新生成 HTML")
            .replace('aria-label="Local docs navigation"', 'aria-label="Developer docs navigation"')
        )
    if path.parent == REFERENCE_DIR:
        return (
            content.replace("../../docs/openrelix-icon.png", "../../openrelix-icon.png")
            .replace("OpenRelix Local Docs", "OpenRelix Developer Docs")
            .replace('aria-label="Local docs navigation"', 'aria-label="Developer docs navigation"')
            .replace('href="product-showcase.html"', 'href="../../product-showcase.html"')
            .replace('href="getting-started.html"', 'href="../../getting-started.html"')
            .replace('href="changelog/v0.x.html"', 'href="../../changelog/v0.x.html"')
        )
    return content


def publicize_visual_guide(content: str) -> str:
    content = (
        content.replace("../docs/openrelix-icon.png", "../openrelix-icon.png")
        .replace("../docs/index.html", "../index.html")
        .replace('data-en="Open the local HTML panel"', 'data-en="Open the HTML panel"')
        .replace("打开本地 HTML 面板", "打开 HTML 面板")
        .replace('data-en="All local HTML docs"', 'data-en="All HTML docs"')
        .replace("全部本地 HTML 文档", "全部 HTML 文档")
    )
    content = content.replace(
        'href="../templates/nightly-summary-schema.json"',
        _with_external_target(f"{GITHUB_SOURCE_ROOT}/blob/main/templates/nightly-summary-schema.json"),
    )
    content = content.replace(
        'href="../templates/"',
        _with_external_target(f"{GITHUB_SOURCE_ROOT}/tree/main/templates"),
    )
    content = re.sub(
        r'href="\.\./scripts/([^"]+)"',
        lambda match: _with_external_target(f"{GITHUB_SOURCE_ROOT}/blob/main/scripts/{match.group(1)}"),
        content,
    )
    content = re.sub(
        r'href="\.\./\.agents/([^"]+)"',
        lambda match: _with_external_target(f"{GITHUB_SOURCE_ROOT}/blob/main/.agents/{match.group(1)}"),
        content,
    )
    content = content.replace(
        'href="../AGENTS.md"',
        _with_external_target(f"{GITHUB_SOURCE_ROOT}/blob/main/AGENTS.md"),
    )
    return content


def build_public_outputs(local_outputs: dict[Path, str]) -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for path, content in local_outputs.items():
        if path == INDEX_PATH or path.parent == REFERENCE_DIR:
            outputs[_public_path_for_local(path)] = publicize_generated_html(path, content)
    if LOCAL_VISUAL_GUIDE_PATH.exists():
        content = LOCAL_VISUAL_GUIDE_PATH.read_text(encoding="utf-8")
        outputs[PUBLIC_VISUAL_GUIDE_PATH] = publicize_visual_guide(content)
    return outputs


def write_outputs(outputs: dict[Path, str], check: bool = False) -> int:
    expected_paths = set(outputs)
    stale_paths = []
    for path, content in outputs.items():
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        stale_paths.append(path)

    existing_generated = set(REFERENCE_DIR.glob("*.html")) if REFERENCE_DIR.exists() else set()
    existing_public_generated = set(PUBLIC_REFERENCE_DIR.glob("*.html")) if PUBLIC_REFERENCE_DIR.exists() else set()
    stale_extra = sorted((existing_generated | existing_public_generated) - expected_paths)

    if check:
        if stale_paths or stale_extra:
            for path in stale_paths:
                print(f"stale: {path.relative_to(REPO_ROOT)}")
            for path in stale_extra:
                print(f"extra: {path.relative_to(REPO_ROOT)}")
            return 1
        print("local docs are up to date")
        return 0

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    for path in stale_extra:
        path.unlink()
        print(f"removed {path.relative_to(REPO_ROOT)}")
    return 0


def docs_snapshot() -> tuple[tuple[str, int, int], ...]:
    snapshot = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        stat = path.stat()
        snapshot.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(snapshot)


def build_once(check: bool = False) -> int:
    documents = load_documents()
    outputs = build_outputs(documents)
    return write_outputs(outputs, check=check)


def watch(interval: float = 1.0) -> int:
    print("watching docs/*.md; press Ctrl-C to stop")
    last_snapshot: tuple[tuple[str, int, int], ...] | None = None
    try:
        while True:
            current_snapshot = docs_snapshot()
            if current_snapshot != last_snapshot:
                result = build_once(check=False)
                if result != 0:
                    return result
                last_snapshot = current_snapshot
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated HTML is stale")
    parser.add_argument("--watch", action="store_true", help="rebuild when docs/*.md changes")
    parser.add_argument("--interval", type=float, default=1.0, help="watch polling interval in seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.watch:
        return watch(interval=args.interval)
    return build_once(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
