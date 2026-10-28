"""Render a small, inert subset of ChatGPT's layout markup. Never evaluate JSX.

Only generated elements/attributes from explicit allowlists reach the PDF browser.
Unknown markup stays visible as text; scripts, handlers and external SVG references
cannot become executable HTML. This is a static document adapter, not a UI runtime.
"""

import html
import re
from html.parser import HTMLParser

CONTAINERS = {"box": "div", "grid": "div", "grid-item": "div", "caption": "div"}
SVG_TAGS = {
    "svg",
    "g",
    "circle",
    "ellipse",
    "rect",
    "path",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "title",
}
SVG_ATTRS = {
    "viewbox": "viewBox",
    "width": "width",
    "height": "height",
    "x": "x",
    "y": "y",
    "x1": "x1",
    "x2": "x2",
    "y1": "y1",
    "y2": "y2",
    "cx": "cx",
    "cy": "cy",
    "r": "r",
    "rx": "rx",
    "ry": "ry",
    "d": "d",
    "points": "points",
    "fill": "fill",
    "stroke": "stroke",
    "fillopacity": "fill-opacity",
    "fill-opacity": "fill-opacity",
    "strokeopacity": "stroke-opacity",
    "stroke-opacity": "stroke-opacity",
    "strokewidth": "stroke-width",
    "stroke-width": "stroke-width",
    "fontsize": "font-size",
    "font-size": "font-size",
    "fontweight": "font-weight",
    "font-weight": "font-weight",
    "textanchor": "text-anchor",
    "text-anchor": "text-anchor",
    "dominantbaseline": "dominant-baseline",
    "dominant-baseline": "dominant-baseline",
    "transform": "transform",
    "opacity": "opacity",
}


class LayoutParser(HTMLParser):
    def __init__(self, engine):
        super().__init__(convert_charrefs=True)
        self.engine = engine
        self.output = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in CONTAINERS:
            target = CONTAINERS[tag]
            classes = ["rich-" + tag]
            if attributes.get("align") == "center":
                classes.append("rich-center")
            self.output.append(f'<{target} class="{" ".join(classes)}">')
        elif tag == "vault-math":
            target = "div"
            self.output.append('<div class="math block">')
        elif tag in SVG_TAGS:
            target = tag
            safe_attrs = []
            for key, value in attrs:
                if key not in SVG_ATTRS or value is None or len(value) > 10000:
                    continue
                # No URLs, CSS, script syntax, or entities can enter attribute values.
                if not re.fullmatch(r"[a-zA-Z0-9#.,%+()\s-]+", value):
                    continue
                if re.search(r"url|javascript|data|https?", value, re.I):
                    continue
                safe_attrs.append(f'{SVG_ATTRS[key]}="{html.escape(value, quote=True)}"')
            if tag == "svg":
                safe_attrs.append('xmlns="http://www.w3.org/2000/svg"')
            self.output.append(f"<{tag} {' '.join(safe_attrs)}>")
        else:
            target = None
            self.output.append(html.escape(self.get_starttag_text()))
        self.stack.append((tag, target))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        # Malformed markup never gets to close generated message/document containers.
        if not self.stack or self.stack[-1][0] != tag:
            self.output.append(html.escape(f"</{tag}>"))
            return
        _, target = self.stack.pop()
        self.output.append(f"</{target}>" if target else html.escape(f"</{tag}>"))

    def handle_data(self, data):
        if any(tag in SVG_TAGS or tag == "vault-math" for tag, _ in self.stack):
            self.output.append(html.escape(data))
        elif data.strip():
            self.output.append(self.engine.renderInline(data.strip()))

    def finish(self):
        while self.stack:
            _, target = self.stack.pop()
            if target:
                self.output.append(f"</{target}>")
        return "".join(self.output)


def render_layout(source, engine):
    # String.raw is accepted only as literal TeX text; no expression is executed.
    source = re.sub(
        r"<math\s+value=\{\s*String\.raw`([^`]*)`\s*\}\s*/>",
        lambda match: "<vault-math>" + html.escape(match[1]) + "</vault-math>",
        source,
    )
    parser = LayoutParser(engine)
    parser.feed(source)
    return parser.finish()


def layout_plugin(engine):
    def block_rule(state, start_line, end_line, silent):
        first = state.src[
            state.bMarks[start_line] + state.tShift[start_line] : state.eMarks[start_line]
        ]
        match = re.match(r"<(box|grid|svg)\b", first)
        if not match or state.sCount[start_line] - state.blkIndent >= 4:
            return False
        root = match[1]
        depth = 0
        last = start_line
        for line in range(start_line, min(end_line, start_line + 2000)):
            text = state.src[state.bMarks[line] : state.eMarks[line]]
            for closing, self_closing in re.findall(rf"<(/?){root}\b[^>]*?(/?)>", text):
                depth += -1 if closing else (0 if self_closing else 1)
            if depth == 0:
                last = line + 1
                break
        else:
            return False
        if silent:
            return True
        token = state.push("vault_layout", "", 0)
        token.content = state.getLines(start_line, last, state.blkIndent, False)
        token.map = [start_line, last]
        state.line = last
        return True

    engine.block.ruler.before("fence", "vault_layout", block_rule)
    engine.add_render_rule(
        "vault_layout",
        lambda self, tokens, idx, options, env: render_layout(tokens[idx].content, engine),
    )
