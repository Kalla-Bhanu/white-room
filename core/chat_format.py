from __future__ import annotations

import re
from html import escape

from markupsafe import Markup


_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def render_message_html(text: str) -> Markup:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            language_attr = f' data-language="{escape(language)}"' if language else ""
            code_block = escape("\n".join(code_lines))
            blocks.append(
                f'<pre class="chat-code-block"><code{language_attr}>{code_block}</code></pre>'
            )
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1)) + 1
            blocks.append(f"<h{level}>{_render_inline(heading_match.group(2))}</h{level}>")
            index += 1
            continue

        if re.match(r"^>\s+", stripped):
            quote_lines: list[str] = []
            while index < len(lines) and re.match(r"^>\s+", lines[index].strip()):
                quote_lines.append(re.sub(r"^>\s?", "", lines[index].strip()))
                index += 1
            blocks.append(f"<blockquote>{_render_inline(' '.join(quote_lines))}</blockquote>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while index < len(lines) and re.match(r"^[-*]\s+", lines[index].strip()):
                item_text = re.sub(r"^[-*]\s+", "", lines[index].strip())
                items.append(f"<li>{_render_inline(item_text)}</li>")
                index += 1
            blocks.append(f"<ul>{''.join(items)}</ul>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"^\d+\.\s+", lines[index].strip()):
                item_text = re.sub(r"^\d+\.\s+", "", lines[index].strip())
                items.append(f"<li>{_render_inline(item_text)}</li>")
                index += 1
            blocks.append(f"<ol>{''.join(items)}</ol>")
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line or next_line.startswith("```") or re.match(r"^(#{1,3}|[-*]|\d+\.|>)\s+", next_line):
                break
            paragraph_lines.append(next_line)
            index += 1
        blocks.append(f"<p>{_render_inline(' '.join(paragraph_lines))}</p>")

    return Markup("".join(blocks) or "<p></p>")


def _render_inline(text: str) -> str:
    rendered = escape(text)
    rendered = _INLINE_CODE_RE.sub(lambda match: f"<code>{escape(match.group(1))}</code>", rendered)
    rendered = _BOLD_RE.sub(r"<strong>\1</strong>", rendered)
    rendered = _ITALIC_RE.sub(r"<em>\1</em>", rendered)
    return rendered
