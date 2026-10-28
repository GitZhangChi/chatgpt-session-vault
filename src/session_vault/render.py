"""Offline HTML/PDF rendering. Conversation content cannot execute scripts or fetch URLs."""

import html
import json
import re
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from session_vault.chatgpt import VaultError
from session_vault.conversation import Conversation
from session_vault.rich import layout_plugin

STATIC = Path(__file__).parent / "static"


def _highlight(code: str, language: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(language) if language else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return highlight(code, lexer, HtmlFormatter(nowrap=True))


def markdown_engine() -> MarkdownIt:
    engine = MarkdownIt("commonmark", {"html": False, "highlight": _highlight}).enable("table")
    engine.use(dollarmath_plugin, allow_labels=False, allow_space=False)
    engine.use(layout_plugin)

    def protect_table_math(state):
        lines = []
        fence = None
        for line in state.src.splitlines(keepends=True):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if marker:
                value = marker[1]
                if fence is None:
                    fence = value
                elif value[0] == fence[0] and len(value) >= len(fence):
                    fence = None
                lines.append(line)
                continue
            if fence is None and line.lstrip().startswith("|"):
                # Table splitting precedes inline math parsing. Keep its bars in a cell.
                segments = re.split(r"(`+[^`]*`+)", line)
                for index in range(0, len(segments), 2):
                    segments[index] = re.sub(
                        r"\\\((.*?)\\\)|(?<!\\)\$(?!\$)(.+?)(?<!\\)\$",
                        lambda match: re.sub(r"(?<!\\)\|", r"\\vert ", match[0]),
                        segments[index],
                    )
                line = "".join(segments)
            lines.append(line)
        state.src = "".join(lines)

    engine.core.ruler.after("normalize", "protect_table_math", protect_table_math)

    def display_math(state, start_line, end_line, silent):
        start = state.bMarks[start_line] + state.tShift[start_line]
        if state.src[start : state.eMarks[start_line]].strip() != "\\[":
            return False
        for end in range(start_line + 1, end_line):
            line = state.src[state.bMarks[end] : state.eMarks[end]].strip()
            if line == "\\]":
                if silent:
                    return True
                token = state.push("math_block", "math", 0)
                token.content = state.getLines(start_line + 1, end, 0, False).strip()
                token.map = [start_line, end + 1]
                state.line = end + 1
                return True
        return False

    engine.block.ruler.before("fence", "chatgpt_display_math", display_math)

    # ChatGPT commonly emits \(inline\) and \[display\] math instead of dollar delimiters.
    def math_rule(state, silent):
        opening = state.src[state.pos : state.pos + 2]
        if opening not in ("\\(", "\\["):
            return False
        closing = "\\)" if opening == "\\(" else "\\]"
        end = state.src.find(closing, state.pos + 2)
        if end < 0:
            return False
        if not silent:
            token = state.push("math_inline", "math", 0)
            token.content = state.src[state.pos + 2 : end]
        state.pos = end + 2
        return True

    engine.inline.ruler.before("escape", "chatgpt_math", math_rule)

    # Remote images are intentionally not fetched by the PDF renderer.
    def image_rule(tokens, idx, options, env):
        alt = tokens[idx].content or "Image"
        return '<span class="asset-note">[Image: ' + html.escape(alt) + "; not downloaded]</span>"

    engine.add_render_rule(
        "image", lambda self, tokens, idx, options, env: image_rule(tokens, idx, options, env)
    )
    return engine


def render_html(conversation: Conversation) -> str:
    engine = markdown_engine()
    notes = ""
    if conversation.warnings:
        notes = (
            "<aside><strong>Export notes</strong><ul>"
            + "".join(f"<li>{html.escape(w)}</li>" for w in conversation.warnings)
            + "</ul></aside>"
        )
    messages = "".join(
        f'<section class="message {m.role}"><div class="role">{m.role.title()}</div>'
        f'<div class="body">{engine.render(m.text)}</div></section>'
        for m in conversation.messages
    )
    css = (STATIC / "document.css").read_text()
    highlight_css = HtmlFormatter(style="friendly").get_style_defs("pre")
    katex_css = (STATIC / "vendor/katex/katex.min.css").read_text()
    katex_js = (STATIC / "vendor/katex/katex.min.js").read_text()
    # No user-controlled text enters executable JavaScript. Markdown HTML is disabled.
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(conversation.title)}</title>
<style>{katex_css}\n{css}\n{highlight_css}</style></head><body>
<header><div class="eyebrow">CHATGPT SESSION VAULT</div>
<h1>{html.escape(conversation.title)}</h1>
<p class="source">https://chatgpt.com/c/{html.escape(conversation.id)}</p>
<p class="source">{len(conversation.messages)} messages · Current branch · Dates in UTC</p></header>
{notes}<main>{messages}</main><script>{katex_js}</script><script>
document.querySelectorAll('.math.inline,.math.block').forEach(el => {{
  const source = el.textContent;
  try {{ katex.render(source, el, {{throwOnError:false, trust:false,
    strict:'ignore', displayMode:el.classList.contains('block'), maxExpand:1000}}); }}
  catch (_) {{ el.textContent = source; }}
}});
window.vaultMathReady = true;
</script></body></html>"""


def export_pdf(document: str, target: Path) -> None:
    vendor = STATIC / "vendor/katex"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PlaywrightError:
                browser = p.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page()

                # Serve only our bundled fonts. No user-provided URL is visited.
                def route_request(route):
                    url = route.request.url
                    prefix = "https://vault.invalid/fonts/"
                    if url.startswith(prefix):
                        name = url[len(prefix) :]
                        if "/" not in name and name.endswith((".woff2", ".woff", ".ttf")):
                            font = vendor / "fonts" / name
                            if font.is_file():
                                route.fulfill(path=str(font))
                                return
                    route.abort()

                page.route("**/*", route_request)
                # CSS font URLs are relative to this base; all resources stay local.
                page.set_content(
                    document.replace("<head>", '<head><base href="https://vault.invalid/">', 1)
                )
                page.wait_for_function("window.vaultMathReady === true")
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="print")
                page.pdf(
                    path=str(target),
                    format="A4",
                    print_background=True,
                    margin={"top": "17mm", "bottom": "19mm", "left": "16mm", "right": "16mm"},
                    display_header_footer=True,
                    header_template="<span></span>",
                    footer_template='<div style="font-size:9px;color:#777;width:100%;'
                    'text-align:center">Session Vault · <span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></div>',
                )
            finally:
                browser.close()
    except PlaywrightError:
        raise VaultError(
            "PDF browser unavailable. Run `uv run playwright install chromium` "
            "(Linux: add --with-deps), or install Google Chrome."
        ) from None


def write_archive(conversation: Conversation, raw: dict, directory: Path) -> dict:
    name = conversation.filename
    document = render_html(conversation)
    pdf = directory / (name + ".pdf")
    export_pdf(document, pdf)
    (directory / (name + ".md")).write_text(conversation.markdown(), encoding="utf-8")
    (directory / (name + ".json")).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "pdf": pdf.name,
        "markdown": name + ".md",
        "json": name + ".json",
        "message_count": len(conversation.messages),
        "warnings": conversation.warnings,
    }
