"""Convert Archives of Nethys markdown into plain text suitable for verbatim quoting."""

import html
import re

_TITLE_RE = re.compile(r"<title\b[^>]*>.*?</title>", re.DOTALL)
_TRAITS_RE = re.compile(r"<traits>.*?</traits>", re.DOTALL)
_ACTIONS_RE = re.compile(r'<actions string="([^"]*)"\s*/>')
_BR_RE = re.compile(r"<br\s*/?>\n?")
_TAG_RE = re.compile(r"</?[a-zA-Z][\w-]*(?:\s[^<>]*)?/?>")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SOURCE_LINE_RE = re.compile(r"^\*\*Source\*\*.*$", re.MULTILINE)
_RULE_LINE_RE = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)
# Markdown italics: _spell name_ and *word*, but not snake_case or dice like 2d6*2.
_ITALIC_US_RE = re.compile(r"(?<![\w_])_([^_\n]+?)_(?![\w_])")
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_LEADING_WS_RE = re.compile(r"^[ \t]+", re.MULTILINE)
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# AoN action glyph names -> compact text form used in the stored rules text.
_ACTION_GLYPHS = {
    "Single Action": "[one-action]",
    "Two Actions": "[two-actions]",
    "Three Actions": "[three-actions]",
    "Reaction": "[reaction]",
    "Free Action": "[free-action]",
}


def markdown_to_text(markdown: str) -> str:
    text = _TITLE_RE.sub("", markdown)
    text = _TRAITS_RE.sub("", text)
    text = _ACTIONS_RE.sub(lambda m: _ACTION_GLYPHS.get(m.group(1), ""), text)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _SOURCE_LINE_RE.sub("", text)
    text = _RULE_LINE_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = text.replace("**", "")
    text = _ITALIC_US_RE.sub(r"\1", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = html.unescape(text)
    text = _TRAILING_WS_RE.sub("", text)
    text = _LEADING_WS_RE.sub("", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
