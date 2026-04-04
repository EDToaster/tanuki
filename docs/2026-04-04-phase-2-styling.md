# Phase 2: Style Normalization

**Date:** 2026-04-04  
**Phase:** 2 of 6  
**Depends on:** Phase 1 (core foundation must be complete)

**Goal:** Implement the full HTML normalization pipeline so EPUBs from different publishers render consistently. Add the asset proxy endpoint for EPUB images. Replace the basic fragment CSS with the full unified reader stylesheet.

**What this phase adds:**
- HTML allowlist (strip unknown/dangerous tags, keep structural/semantic ones)
- Inline style allowlist (keep only `font-style`, `font-weight`, `vertical-align: sub/super`)
- `<br>` run collapse (2+ consecutive `<br>` → paragraph break)
- `<font>` tag unwrapping
- Image path rewriting (`../images/foo.jpg` → `/book/:id/asset/:path`)
- New `/book/:id/asset/:path` backend endpoint (streams images from EPUB ZIP)
- SVG single-image wrapper detection + conversion to `<img>`
- Unified reader CSS (system serif with CJK fallbacks, dark mode, ruby/furigana)
- Ruby/furigana preservation with correct `.w` interaction fix

---

### Task 1: Asset endpoint for EPUB images

**Files:** `server.py`, `tests/test_server.py`

EPUB images are stored inside the ZIP. After chapter extraction, relative `src="../images/foo.jpg"` paths are broken. This endpoint serves images from inside the EPUB.

**Step 1: Write failing tests**

```python
def make_epub_with_image(image_bytes=b'\x89PNG\r\n\x1a\n' + b'\x00' * 20):
    """Build a minimal EPUB with an image in the manifest."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        z.writestr('OEBPS/content.opf', '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>T</dc:title><dc:creator>A</dc:creator><dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="c0" href="chapter0.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="images/cover.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c0"/></spine>
</package>''')
        z.writestr('OEBPS/chapter0.xhtml',
            '<html><body><p>Hello</p><img src="../images/cover.png"/></body></html>')
        z.writestr('OEBPS/images/cover.png', image_bytes)
    buf.seek(0)
    return buf.read()

def test_asset_endpoint_serves_image(client, tmp_path):
    epub_data = make_epub_with_image()
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/asset/OEBPS/images/cover.png')
    assert r.status_code == 200
    assert r.content_type.startswith('image/')

def test_asset_endpoint_404_for_missing_file(client, tmp_path):
    epub_data = make_epub_with_image()
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/asset/OEBPS/images/missing.png')
    assert r.status_code == 404

def test_asset_endpoint_rejects_path_traversal(client, tmp_path):
    epub_data = make_epub_with_image()
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/asset/../../../etc/passwd')
    assert r.status_code in (400, 404)
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "asset" -v
```

**Step 3: Implement**

```python
import mimetypes

@app.route('/book/<book_id>/asset/<path:asset_path>')
def asset(book_id, asset_path):
    _validate_book_id(book_id)
    # Prevent path traversal inside the ZIP entry name
    if '..' in asset_path or asset_path.startswith('/'):
        abort(400)
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    try:
        with _open_epub(epub_path.read_bytes()) as z:
            data = z.read(asset_path)
    except KeyError:
        abort(404)
    mime = mimetypes.guess_type(asset_path)[0] or 'application/octet-stream'
    return Response(data, mimetype=mime)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: asset endpoint for EPUB images"
```

---

### Task 2: HTML normalization pipeline

**Files:** `server.py`, `tests/test_server.py`

Replace ad-hoc `_sanitize_html` from Phase 1 with a comprehensive normalization function that handles the full allowlist pipeline.

**Step 1: Write failing tests**

```python
from server import normalize_html

def test_normalize_strips_script_and_style():
    html = '<p>Hello</p><script>alert(1)</script><style>p{color:red}</style>'
    result = normalize_html(html, 'mybook', 'zh')
    assert '<script>' not in result
    assert '<style>' not in result
    assert 'Hello' in result

def test_normalize_strips_on_attrs():
    html = '<p onclick="evil()">Text</p><img onerror="evil()" src="x.png"/>'
    result = normalize_html(html, 'mybook', 'zh')
    assert 'onclick' not in result
    assert 'onerror' not in result

def test_normalize_strips_javascript_href():
    html = '<a href="javascript:alert(1)">Click</a>'
    result = normalize_html(html, 'mybook', 'zh')
    assert 'javascript:' not in result

def test_normalize_keeps_semantic_inline_styles():
    html = '<p style="font-style:italic;color:red;font-size:14px">Text</p>'
    result = normalize_html(html, 'mybook', 'zh')
    assert 'font-style:italic' in result or 'font-style: italic' in result
    assert 'color' not in result
    assert 'font-size' not in result

def test_normalize_strips_class_attrs():
    html = '<p class="epub-chapter-body">Text</p>'
    result = normalize_html(html, 'mybook', 'zh')
    assert 'class=' not in result

def test_normalize_unwraps_font_tag():
    html = '<p><font face="Times" color="red">Text</font></p>'
    result = normalize_html(html, 'mybook', 'zh')
    assert '<font' not in result
    assert 'Text' in result

def test_normalize_collapses_br_runs():
    html = '<p>First</p><br/><br/><br/><p>Second</p>'
    result = normalize_html(html, 'mybook', 'zh')
    assert result.count('<br') <= 1

def test_normalize_rewrites_image_paths():
    html = '<img src="../images/cover.png" alt="cover"/>'
    result = normalize_html(html, 'mybook', 'zh', chapter_base='OEBPS')
    assert '/book/mybook/asset/' in result

def test_normalize_strips_img_dimensions():
    html = '<img src="../images/foo.png" width="600" height="400"/>'
    result = normalize_html(html, 'mybook', 'zh', chapter_base='OEBPS')
    assert 'width=' not in result
    assert 'height=' not in result

def test_normalize_converts_svg_image_wrapper():
    html = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400">
      <image xlink:href="../images/illus.jpg" width="600" height="400"/>
    </svg>'''
    result = normalize_html(html, 'mybook', 'zh', chapter_base='OEBPS')
    assert '<img' in result
    assert '<svg' not in result

def test_normalize_preserves_ruby():
    html = '<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>'
    result = normalize_html(html, 'mybook', 'ja')
    assert '<ruby>' in result
    assert '<rt>' in result
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "normalize" -v
```

**Step 3: Implement**

Replace the old `_sanitize_html` with the full `normalize_html` pipeline:

```python
import posixpath

# Inline style properties that carry semantic meaning and should be preserved.
_STYLE_ALLOWLIST = {'font-style', 'font-weight', 'vertical-align'}
_VERTICAL_ALIGN_VALUES = {'sub', 'super'}

def _parse_inline_style(style_str: str) -> dict:
    """Parse 'font-style:italic; color:red' → {'font-style': 'italic', 'color': 'red'}"""
    result = {}
    for decl in style_str.split(';'):
        decl = decl.strip()
        if ':' in decl:
            prop, _, val = decl.partition(':')
            result[prop.strip().lower()] = val.strip()
    return result

def _filter_inline_style(style_str: str) -> str | None:
    """Return filtered style string, or None if nothing survives."""
    props = _parse_inline_style(style_str)
    kept = []
    for prop, val in props.items():
        if prop not in _STYLE_ALLOWLIST:
            continue
        if prop == 'vertical-align' and val not in _VERTICAL_ALIGN_VALUES:
            continue
        kept.append(f'{prop}:{val}')
    return '; '.join(kept) if kept else None

def _rewrite_epub_src(src: str, book_id: str, chapter_base: str) -> str:
    """Convert EPUB-relative src to /book/{id}/asset/{internal_path}."""
    if src.startswith('data:') or src.startswith('http'):
        return src
    # Resolve relative to chapter base directory
    resolved = posixpath.normpath(posixpath.join(chapter_base, src))
    # Remove leading slash if present
    resolved = resolved.lstrip('/')
    return f'/book/{book_id}/asset/{resolved}'

def normalize_html(html: str, book_id: str, lang: str, chapter_base: str = 'OEBPS') -> str:
    """
    Full normalization pipeline:
    1. Security sanitization (script, on*, javascript:)
    2. Unwrap <font> tags
    3. Filter inline styles to allowlist
    4. Strip class and dimension attrs
    5. Collapse <br> runs
    6. Convert SVG single-image wrappers
    7. Rewrite image src paths
    8. (Tag allowlist enforcement is handled by stripping unknown dangerous elements)
    """
    soup = BeautifulSoup(html, 'lxml')

    # 1. Security: strip executable elements
    for tag in soup.find_all(['script', 'style', 'iframe', 'object', 'embed']):
        tag.decompose()

    # 2. Unwrap <font> tags (obsolete, carries only stripped styling)
    for tag in soup.find_all('font'):
        tag.unwrap()

    # 3-4. Attribute normalization on remaining tags
    for tag in soup.find_all(True):
        attrs_to_remove = []
        for attr in list(tag.attrs):
            if attr.startswith('on'):
                attrs_to_remove.append(attr)
            elif attr == 'class':
                attrs_to_remove.append(attr)
            elif attr in ('align', 'valign', 'hspace', 'vspace'):
                attrs_to_remove.append(attr)
            elif attr == 'href':
                val = str(tag.get('href', '')).strip().lower()
                if val.startswith('javascript:'):
                    attrs_to_remove.append(attr)
            elif attr == 'src':
                val = str(tag.get('src', '')).strip().lower()
                if val.startswith('javascript:'):
                    attrs_to_remove.append(attr)
            elif attr == 'style':
                filtered = _filter_inline_style(str(tag.get('style', '')))
                if filtered:
                    tag['style'] = filtered
                else:
                    attrs_to_remove.append(attr)

        for attr in attrs_to_remove:
            del tag[attr]

        # Strip width/height from img
        if tag.name == 'img':
            tag.attrs.pop('width', None)
            tag.attrs.pop('height', None)
            # Rewrite src
            src = tag.get('src', '')
            if src and not src.startswith('/book/') and not src.startswith('http'):
                tag['src'] = _rewrite_epub_src(src, book_id, chapter_base)

    # 5. Collapse runs of 2+ <br> tags
    for br in soup.find_all('br'):
        siblings = list(br.next_siblings)
        count = 1
        to_remove = []
        for sib in siblings:
            if hasattr(sib, 'name') and sib.name == 'br':
                count += 1
                to_remove.append(sib)
            elif hasattr(sib, 'string') and not str(sib).strip():
                to_remove.append(sib)
            else:
                break
        if count >= 2:
            for r in to_remove:
                r.decompose()
            # Replace the first br with a paragraph break marker
            br.replace_with(BeautifulSoup('<p></p>', 'lxml').find('p'))

    # 6. Convert SVG single-image wrappers to <img>
    for svg in soup.find_all('svg'):
        children = [c for c in svg.children if hasattr(c, 'name') and c.name is not None]
        if len(children) == 1 and children[0].name == 'image':
            img_tag = children[0]
            href = img_tag.get('xlink:href') or img_tag.get('href') or ''
            new_img = soup.new_tag('img', alt='')
            new_img['src'] = _rewrite_epub_src(href, book_id, chapter_base) if href else ''
            svg.replace_with(new_img)
        else:
            svg.decompose()

    body = soup.find('body')
    return body.decode_contents() if body else str(soup)
```

Update `extract_chapter` to call `normalize_html` instead of `_sanitize_html`:

```python
def extract_chapter(data: bytes, index: int, book_id: str = '') -> str | None:
    paths = get_chapter_paths(data)
    if index >= len(paths):
        return None
    chapter_path = paths[index]
    # Derive chapter base directory for image path resolution
    chapter_base = chapter_path.rsplit('/', 1)[0] if '/' in chapter_path else ''
    with _open_epub(data) as z:
        raw = z.read(chapter_path).decode('utf-8', errors='replace')
    soup = BeautifulSoup(raw, 'lxml')
    body = soup.find('body')
    content = body.decode_contents() if body else raw
    if not content.strip():
        content = '<p class="chapter-empty">Chapter has no readable content.</p>'
    meta = parse_epub_metadata(data)
    return normalize_html(content, book_id, meta['language'], chapter_base)

@app.route('/book/<book_id>/chapter/<int:index>')
def chapter(book_id, index):
    _validate_book_id(book_id)
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    try:
        data = epub_path.read_bytes()
        content = extract_chapter(data, index, book_id)
    except Exception:
        abort(500)
    if content is None:
        abort(404)
    meta = parse_epub_metadata(data)
    wrapped = wrap_cjk(content, meta['language'])
    return Response(wrapped, mimetype='text/html; charset=utf-8')
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: full HTML normalization pipeline with allowlist, image rewriting, SVG conversion"
```

---

### Task 3: Unified reader CSS

**Files:** `static/style.css`

Replace the basic reader font/layout styles from Phase 1 with the full system-serif CJK reader stylesheet. This is additive CSS that targets `article[data-lang]` elements inside `.chapter-content`.

**Step 1: Ensure chapter endpoint wraps output in `<article data-lang="...">`**

Update the chapter route in `server.py` to wrap the fragment:

```python
@app.route('/book/<book_id>/chapter/<int:index>')
def chapter(book_id, index):
    _validate_book_id(book_id)
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    try:
        data = epub_path.read_bytes()
        content = extract_chapter(data, index, book_id)
    except Exception:
        abort(500)
    if content is None:
        abort(404)
    meta = parse_epub_metadata(data)
    lang = meta['language']
    wrapped = wrap_cjk(content, lang)
    fragment = f'<article data-lang="{lang}">{wrapped}</article>'
    return Response(fragment, mimetype='text/html; charset=utf-8')
```

**Step 2: Append full reader CSS to `static/style.css`**

```css
/* ===================================================================
   EPUB Reader Stylesheet
   Applied to: .chapter-content > article[data-lang]
   =================================================================== */

article[data-lang] {
  font-family:
    "Hiragino Mincho ProN",
    "Hiragino Mincho Pro",
    "Yu Mincho", "YuMincho",
    "Noto Serif CJK SC",
    "Noto Serif CJK JP",
    "Noto Serif CJK KR",
    "Songti SC", "STSong",
    "SimSun", "NSimSun",
    "AppleMyungjo",
    "Batang", "BatangChe",
    Georgia,
    serif;
  font-size: 1.1rem;
  line-height: 1.8;
  color: var(--fg);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* CJK language-specific rules */
article[data-lang="zh"],
article[data-lang="zh-hans"],
article[data-lang="zh-hant"],
article[data-lang="zh-TW"],
article[data-lang="zh-HK"] {
  line-break: strict;
  text-justify: inter-character;
  word-break: normal;
  font-variant-east-asian: proportional-width;
}

article[data-lang="ja"] {
  line-break: strict;
  text-justify: inter-character;
  word-break: normal;
  font-variant-east-asian: proportional-width;
}

article[data-lang="ko"] {
  word-break: keep-all;
  text-justify: auto;
  line-break: strict;
}

/* Paragraphs */
article[data-lang] p {
  margin-top: 0;
  margin-bottom: 0.9em;
  orphans: 2;
  widows: 2;
}

article[data-lang] blockquote > p:last-child,
article[data-lang] li > p:last-child { margin-bottom: 0; }

/* Headings */
article[data-lang] h1, article[data-lang] h2,
article[data-lang] h3, article[data-lang] h4,
article[data-lang] h5, article[data-lang] h6 {
  font-weight: 700;
  line-height: 1.3;
  margin-top: 1.6em;
  margin-bottom: 0.4em;
  break-after: avoid;
  page-break-after: avoid;
}
article[data-lang] h1 { font-size: 1.6em; margin-top: 0.5em; }
article[data-lang] h2 { font-size: 1.35em; }
article[data-lang] h3 { font-size: 1.15em; }
article[data-lang] h4 { font-size: 1.05em; }
article[data-lang] h5 { font-size: 1em; font-style: italic; }
article[data-lang] h6 { font-size: 0.9em; color: #888; }

/* Blockquote */
article[data-lang] blockquote {
  margin: 1em 0;
  padding: 0.75em 1.25em;
  background: rgba(0,0,0,0.04);
  border-left: 3px solid #ddd;
  border-radius: 0 4px 4px 0;
  color: #555;
  font-style: italic;
}

/* Lists */
article[data-lang] ul, article[data-lang] ol { margin: 0.5em 0 0.9em 0; padding-left: 1.5em; }
article[data-lang] li { margin-bottom: 0.25em; line-height: 1.8; }
article[data-lang] dl { margin: 0.5em 0; }
article[data-lang] dt { font-weight: 700; margin-top: 0.5em; }
article[data-lang] dd { margin-left: 1.5em; margin-bottom: 0.25em; }

/* Images — max-width and break-inside */
article[data-lang] img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;
  break-inside: avoid;
  page-break-inside: avoid;
}
article[data-lang] figure { margin: 1em 0; break-inside: avoid; page-break-inside: avoid; }
article[data-lang] figcaption { font-size: 0.85em; color: #888; text-align: center; margin-top: 0.4em; font-style: italic; }

/* HR */
article[data-lang] hr { border: none; border-top: 1px solid #ddd; margin: 1.5em auto; width: 60%; }

/* Links */
article[data-lang] a { color: var(--accent); text-decoration: underline; text-underline-offset: 0.15em; }

/* Code */
article[data-lang] pre {
  background: rgba(0,0,0,0.05);
  border: 1px solid #ddd;
  border-radius: 4px;
  padding: 0.75em 1em;
  overflow-x: auto;
  font-size: 0.85em;
  line-height: 1.5;
  white-space: pre;
  break-inside: avoid;
}
article[data-lang] code {
  background: rgba(0,0,0,0.05);
  border-radius: 3px;
  padding: 0.1em 0.3em;
  font-size: 0.88em;
  font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
}
article[data-lang] pre code { background: none; padding: 0; font-size: inherit; }

/* Tables */
article[data-lang] table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.9em; break-inside: avoid; }
article[data-lang] th, article[data-lang] td { border: 1px solid #ddd; padding: 0.4em 0.6em; text-align: left; vertical-align: top; }
article[data-lang] th { background: rgba(0,0,0,0.04); font-weight: 700; }

/* Ruby / Furigana
 * .w wrapping fix: do NOT wrap individual characters inside <ruby>.
 * The entire <ruby> element is wrapped as one .w unit in Phase 4.
 * This prevents rt annotations detaching from their base characters.
 */
article[data-lang] ruby { ruby-align: center; }
article[data-lang] rt {
  font-size: 0.5em;
  font-family: "Hiragino Kaku Gothic ProN", "Hiragino Sans",
    "Noto Sans CJK JP", "Noto Sans CJK SC", "Yu Gothic", sans-serif;
  color: #888;
  font-style: normal;
  font-weight: normal;
  line-height: 1.2;
  letter-spacing: 0;
}
/* Extra line height when ruby is present to prevent overlapping */
article[data-lang] p:has(ruby) { line-height: 2.2; }

/* Misc inline */
article[data-lang] mark { background: rgba(255,220,0,0.3); color: inherit; border-radius: 2px; padding: 0 0.15em; }
article[data-lang] sub, article[data-lang] sup { font-size: 0.7em; line-height: 0; }
article[data-lang] abbr[title] { text-decoration: underline dotted; cursor: help; }

/* Dark mode overrides */
@media (prefers-color-scheme: dark) {
  article[data-lang] blockquote { background: rgba(255,255,255,0.06); border-color: #444; color: #aaa; }
  article[data-lang] pre { background: rgba(255,255,255,0.06); border-color: #444; }
  article[data-lang] code { background: rgba(255,255,255,0.06); }
  article[data-lang] th { background: rgba(255,255,255,0.06); }
  article[data-lang] th, article[data-lang] td { border-color: #444; }
  article[data-lang] hr { border-color: #444; }
}
```

**Step 3: Update `app.js` to target `article[data-lang]` for `loadChapter`**

The `content.innerHTML = html` now receives `<article data-lang="...">...</article>` — no change needed in JS since it's just setting `innerHTML`. The CSS selectors in `style.css` handle the rest.

**Step 4: Verify in browser**

```bash
python server.py
# Open a Chinese/Korean book — verify:
# - Consistent serif font renders (system-specific)
# - Images don't overflow beyond column width
# - Dark mode switches correctly on OS toggle
# - Ruby annotations (if present) render with annotation above base
```

**Step 5: Commit**

```bash
git add server.py static/style.css
git commit -m "feat: unified reader CSS with CJK serif stack, ruby support, dark mode"
```

---

### Task 4: Run full test suite

**Step 1:**

```bash
pytest tests/ -v
```

Expected: all PASS

**Step 2: Test with real illustrated EPUB**

```bash
python server.py
# Open a book with inline images — verify images render within columns (not overflowing)
# Verify dark mode appearance with OS setting
```

**Step 3: Commit**

```bash
git commit -m "chore: verified Phase 2 complete — normalization + unified CSS"
```
