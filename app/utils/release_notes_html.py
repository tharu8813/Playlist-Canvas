"""Render GitHub-flavored release notes to QTextDocument-friendly HTML.

``QTextBrowser.setMarkdown`` ignores GitHub admonitions (``> [!NOTE]``) and
offers no styling hooks, so release notes looked flat and showed literal
``[!CAUTION]`` text. This converter covers the block and inline elements that
actually appear in release notes and wraps them in the small HTML/CSS subset
that ``QTextDocument`` understands.
"""

from __future__ import annotations

from html import escape
import re

# emoji, Korean label, English label, then (light accent, dark accent).
_ADMONITIONS = {
    "NOTE": ("ℹ️", "참고", "Note", "#0969DA", "#58A6FF"),
    "TIP": ("\U0001F4A1", "팁", "Tip", "#1A7F37", "#3FB950"),
    "IMPORTANT": ("❗", "중요", "Important", "#8250DF", "#BC8CFF"),
    "WARNING": ("⚠️", "주의", "Warning", "#9A6700", "#D29922"),
    "CAUTION": ("\U0001F6D1", "경고", "Caution", "#CF222E", "#FF7B72"),
}
_QUOTE_BAR = ("#8B949E", "#6E7681")

_ADMONITION_START = re.compile(r"^>\s*\[!(\w+)\]\s*$")
_ORDERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_UNORDERED = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_TASK = re.compile(r"^\[( |x|X)\]\s+(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

_CODE_SPAN = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])|(?<![_\w])_([^_\n]+)_(?![_\w])")
_STRIKE = re.compile(r"~~(.+?)~~")


def _inline(text: str) -> str:
    """Convert inline markdown in one line of already-block-classified text."""
    stashed: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stashed.append(f"<code>{escape(match.group(1))}</code>")
        return f"\x00{len(stashed) - 1}\x00"

    text = _CODE_SPAN.sub(_stash, text)
    text = escape(text, quote=False)
    text = _IMAGE.sub(
        lambda m: (
            f'<img src="{escape(m.group(2), quote=True)}" '
            f'alt="{escape(m.group(1), quote=True)}">'
        ),
        text,
    )
    text = _LINK.sub(
        lambda m: (
            f'<a href="{escape(m.group(2), quote=True)}">{m.group(1)}</a>'
        ),
        text,
    )
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _STRIKE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    for index, snippet in enumerate(stashed):
        text = text.replace(f"\x00{index}\x00", snippet)
    return text


def _list_indent(prefix: str) -> int:
    return len(prefix.replace("\t", "    ")) // 2


class _Renderer:
    def __init__(self, korean: bool, dark: bool = False) -> None:
        self.korean = korean
        self.dark = dark
        self.out: list[str] = []
        self._list_stack: list[str] = []

    # -- list handling ------------------------------------------------
    def _close_lists(self, to_depth: int = 0) -> None:
        while len(self._list_stack) > to_depth:
            self.out.append(f"</{self._list_stack.pop()}>")

    def _open_list(self, depth: int, tag: str) -> None:
        while len(self._list_stack) < depth + 1:
            self._list_stack.append(tag)
            self.out.append(f"<{tag}>")
        if self._list_stack[depth] != tag:
            self._close_lists(depth)
            self._list_stack.append(tag)
            self.out.append(f"<{tag}>")

    def _list_item(self, content: str) -> None:
        task = _TASK.match(content)
        if task:
            box = "☑" if task.group(1).lower() == "x" else "☐"
            self.out.append(f"<li>{box} {_inline(task.group(2))}</li>")
        else:
            self.out.append(f"<li>{_inline(content)}</li>")

    # -- entry point ------------------------------------------------
    def render(self, markdown: str) -> str:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()

            if not stripped:
                self._close_lists()
                index += 1
                continue

            admonition = _ADMONITION_START.match(line)
            if admonition and admonition.group(1).upper() in _ADMONITIONS:
                index = self._emit_admonition(lines, index, admonition.group(1))
                continue

            if stripped.startswith("```") or stripped.startswith("~~~"):
                index = self._emit_code_block(lines, index, stripped[:3])
                continue

            heading = _HEADING.match(line)
            if heading:
                self._close_lists()
                level = len(heading.group(1))
                self.out.append(
                    f"<h{level}>{_inline(heading.group(2))}</h{level}>"
                )
                index += 1
                continue

            if _RULE.match(line):
                self._close_lists()
                self.out.append("<hr>")
                index += 1
                continue

            if (
                "|" in line
                and index + 1 < len(lines)
                and _TABLE_DIVIDER.match(lines[index + 1])
            ):
                index = self._emit_table(lines, index)
                continue

            ordered = _ORDERED.match(line)
            unordered = _UNORDERED.match(line)
            if ordered or unordered:
                match = ordered or unordered
                depth = _list_indent(match.group(1))
                tag = "ol" if ordered else "ul"
                self._open_list(depth, tag)
                self._close_lists(depth + 1)
                self._list_item(match.group(3 if ordered else 2))
                index += 1
                continue

            if stripped.startswith(">"):
                index = self._emit_quote(lines, index)
                continue

            self._close_lists()
            paragraph = [stripped]
            index += 1
            while index < len(lines) and lines[index].strip() and not _is_block_start(
                lines[index]
            ):
                paragraph.append(lines[index].strip())
                index += 1
            self.out.append(f"<p>{_inline(' '.join(paragraph))}</p>")

        self._close_lists()
        return "".join(self.out)

    def _accent_bar(self, color: str, inner: str, label: str = "") -> str:
        """A left colour bar plus content, with no background fill.

        Filling the callout with a fixed light colour hid its text in dark
        mode; leaving the body on the widget's own surface keeps it readable in
        both themes, and only the thin bar and the label carry the accent.
        """
        heading = (
            f'<p style="color:{color};margin:0 0 2px 0;"><b>{label}</b></p>'
            if label else ""
        )
        return (
            '<table width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:8px 0;"><tr>'
            f'<td width="4" bgcolor="{color}"></td><td width="10"></td>'
            f"<td>{heading}{inner}</td></tr></table>"
        )

    # -- block builders ------------------------------------------------
    def _emit_admonition(self, lines: list[str], index: int, kind: str) -> int:
        emoji, korean_label, english_label, light, dark = _ADMONITIONS[
            kind.upper()
        ]
        color = dark if self.dark else light
        label = f"{emoji} {escape(korean_label if self.korean else english_label)}"
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].lstrip().startswith(">"):
            body.append(lines[index].lstrip()[1:].lstrip())
            index += 1
        inner = _Renderer(self.korean, self.dark).render("\n".join(body).strip())
        self.out.append(self._accent_bar(color, inner, label))
        return index

    def _emit_code_block(self, lines: list[str], index: int, fence: str) -> int:
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip()[:3] != fence:
            body.append(lines[index])
            index += 1
        index += 1  # closing fence
        code = escape("\n".join(body))
        self.out.append(f"<pre><code>{code}</code></pre>")
        return index

    def _emit_quote(self, lines: list[str], index: int) -> int:
        body: list[str] = []
        while index < len(lines) and lines[index].lstrip().startswith(">"):
            body.append(lines[index].lstrip()[1:].lstrip())
            index += 1
        inner = _Renderer(self.korean, self.dark).render("\n".join(body).strip())
        self.out.append(
            self._accent_bar(_QUOTE_BAR[1 if self.dark else 0], inner)
        )
        return index

    def _emit_table(self, lines: list[str], index: int) -> int:
        def cells(row: str) -> list[str]:
            trimmed = row.strip().strip("|")
            return [cell.strip() for cell in trimmed.split("|")]

        header = cells(lines[index])
        index += 2  # header + divider
        rows: list[list[str]] = []
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            rows.append(cells(lines[index]))
            index += 1
        html = [
            '<table border="1" cellpadding="6" cellspacing="0" '
            'style="border-collapse:collapse;margin:8px 0;">'
        ]
        html.append("<tr>")
        for column in header:
            html.append(f"<th>{_inline(column)}</th>")
        html.append("</tr>")
        for row in rows:
            html.append("<tr>")
            for column in range(len(header)):
                value = row[column] if column < len(row) else ""
                html.append(f"<td>{_inline(value)}</td>")
            html.append("</tr>")
        html.append("</table>")
        self.out.append("".join(html))
        return index


def _is_block_start(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _HEADING.match(line)
        or _RULE.match(line)
        or _ORDERED.match(line)
        or _UNORDERED.match(line)
        or stripped.startswith(">")
        or stripped.startswith("```")
        or stripped.startswith("~~~")
        or "|" in line
    )


def render_release_notes_html(
    markdown: str, korean: bool = False, dark: bool = False,
) -> str:
    """Return a styled HTML fragment for the given release-notes markdown.

    ``dark`` only changes accent colours; no block is given a fixed background,
    so body text always sits on the viewer's own surface and stays readable.
    """
    text = (markdown or "").strip()
    if not text:
        return (
            "<p>릴리즈 설명이 없습니다.</p>"
            if korean else "<p>No release notes were provided.</p>"
        )
    return _Renderer(korean, dark).render(text)
