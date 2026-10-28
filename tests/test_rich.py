from session_vault.render import markdown_engine


def test_cards_math_and_svg_render_without_literal_layout_source():
    source = r"""<box border radius="xl" padding={3}>
<grid columns={2}>
<grid-item><box align="center">
<math value={String.raw`\sum_{i=1}^{n}a_i` }/>
**Summation** <caption>Add the elements</caption>
</box></grid-item>
</grid>
<svg viewBox="0 0 330 170" width="100%">
<circle cx="123" cy="85" r="72" fill="#3B82F6" fillOpacity="0.16"/>
<text x="84" y="27" fontSize="14">A</text>
</svg>
</box>"""
    document = markdown_engine().render(source)
    assert 'class="rich-grid"' in document
    assert "<strong>Summation</strong>" in document
    assert '<div class="math block">\\sum_{i=1}^{n}a_i</div>' in document
    assert 'viewBox="0 0 330 170"' in document
    assert 'fill-opacity="0.16"' in document
    assert "&lt;box" not in document
    assert "String.raw" not in document


def test_unsafe_layout_content_is_inert():
    source = """<box onclick="alert(1)">
<script>window.attacked = true</script>
<svg onload="alert(1)"><foreignObject><iframe src="https://evil.invalid"/></foreignObject>
<circle fill="url(https://evil.invalid)" onload="alert(1)"/>
<image href="https://evil.invalid"/></svg>
</box>"""
    document = markdown_engine().render(source)
    assert "<script>" not in document
    assert "<iframe" not in document
    assert "<foreignobject" not in document
    assert "<image " not in document
    assert "<svg onload" not in document
    assert "<circle fill=" not in document
    assert "onclick=" not in document


def test_code_fences_and_incomplete_layout_are_preserved_as_source():
    engine = markdown_engine()
    assert "rich-box" not in engine.render("```html\n<box>Example</box>\n```")
    assert "&lt;box" in engine.render("<box>Unfinished")


def test_display_math_is_a_block():
    document = markdown_engine().render("\\[\n\\sum_{i=1}^n a_i\n\\]")
    assert 'class="math block"' in document
    assert "<p>" not in document


def test_math_bars_do_not_split_table_columns_or_change_code():
    source = r"""| Symbol | Meaning | Example |
|---|---|---|
| \(|A|\) | Cardinality | $|A|=3$ |

```markdown
| \(|A|\) | Code example |
```
"""
    document = markdown_engine().render(source)
    assert document.count("<td>") == 3
    assert r"\vert A\vert " in document
    assert r"\(|A|\)" in document
