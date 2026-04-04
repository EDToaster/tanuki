# Ebook Reader Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a lightweight self-hosted EPUB reader optimized for language learning (Chinese/Korean), with tap-to-lookup and paginated reading.

**Architecture:** Python/Flask backend serves EPUB chapters as pre-processed HTML fragments (CJK characters wrapped in tappable spans). Pure vanilla JS frontend renders chapters using CSS multi-column for pagination. Dictionary lookups hit the Wiktionary API directly from the browser.

**Tech Stack:** Python 3.12, Flask, BeautifulSoup4, pytest — frontend is zero-dependency vanilla JS/CSS.

---

## Project Structure

```
ebook-reader/
├── server.py
├── requirements.txt
├── requirements-dev.txt
├── static/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── tests/
│   ├── conftest.py
│   └── test_server.py
└── books/            # symlink or real dir, .gitignored
```

---

### Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `server.py` (skeleton)
- Create: `tests/conftest.py`
- Create: `tests/test_server.py` (empty)
- Create: `.gitignore`

**Step 1: Create the repo and scaffold**

```bash
mkdir ebook-reader && cd ebook-reader
git init
```

**Step 2: Create `requirements.txt`**

```
flask==3.1.0
beautifulsoup4==4.12.3
lxml==5.3.0
```

**Step 3: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest==8.3.5
pytest-flask==1.3.0
```

**Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
venv/
books/
*.epub
```

**Step 5: Create skeleton `server.py`**

```python
from flask import Flask

app = Flask(__name__, static_folder='static')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
```

**Step 6: Create `tests/conftest.py`**

```python
import pytest
from server import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c
```

**Step 7: Create empty `tests/test_server.py`**

```python
# tests added per task
```

**Step 8: Install deps and verify**

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
pytest  # should collect 0 items, exit 0
```

**Step 9: Commit**

```bash
git add .
git commit -m "chore: initial project scaffold"
```

---

### Task 2: EPUB metadata parsing

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

An EPUB is a ZIP. `META-INF/container.xml` points to the OPF file, which has title/author/language and the spine (ordered chapter list).

**Step 1: Write failing tests**

Add to `tests/test_server.py`:

```python
import zipfile, io, pytest
from server import parse_epub_metadata, get_chapter_paths

def make_epub(chapters=1, language='zh', title='Test Book', author='Test Author'):
    """Build a minimal valid EPUB in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')
        spine_items = ''.join(
            f'<item id="ch{i}" href="chapter{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(chapters)
        )
        spine_refs = ''.join(f'<itemref idref="ch{i}"/>' for i in range(chapters))
        z.writestr('OEBPS/content.opf', f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>
  </metadata>
  <manifest>{spine_items}</manifest>
  <spine>{spine_refs}</spine>
</package>''')
        for i in range(chapters):
            z.writestr(f'OEBPS/chapter{i}.xhtml',
                f'<html><body><p>Chapter {i} content 你好</p></body></html>')
    buf.seek(0)
    return buf.read()

def test_parse_epub_metadata_returns_title_author_language():
    data = make_epub(language='zh', title='三体', author='刘慈欣')
    meta = parse_epub_metadata(data)
    assert meta['title'] == '三体'
    assert meta['author'] == '刘慈欣'
    assert meta['language'] == 'zh'

def test_get_chapter_paths_returns_ordered_list():
    data = make_epub(chapters=3)
    paths = get_chapter_paths(data)
    assert len(paths) == 3
    # All paths should be strings (relative paths within the ZIP)
    assert all(isinstance(p, str) for p in paths)
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -v
```
Expected: `ImportError: cannot import name 'parse_epub_metadata'`

**Step 3: Implement in `server.py`**

```python
import zipfile, io
import xml.etree.ElementTree as ET
from flask import Flask

app = Flask(__name__, static_folder='static')

NS = {
    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
}

def _open_epub(data: bytes):
    return zipfile.ZipFile(io.BytesIO(data))

def _opf_root(z: zipfile.ZipFile):
    container = ET.fromstring(z.read('META-INF/container.xml'))
    opf_path = container.find('.//container:rootfile', NS).get('full-path')
    return ET.fromstring(z.read(opf_path)), opf_path.rsplit('/', 1)[0]

def parse_epub_metadata(data: bytes) -> dict:
    with _open_epub(data) as z:
        root, _ = _opf_root(z)
        meta = root.find('opf:metadata', NS)
        return {
            'title': meta.findtext('dc:title', 'Unknown', NS),
            'author': meta.findtext('dc:creator', 'Unknown', NS),
            'language': meta.findtext('dc:language', '', NS),
        }

def get_chapter_paths(data: bytes) -> list[str]:
    with _open_epub(data) as z:
        root, base = _opf_root(z)
        manifest = {
            item.get('id'): item.get('href')
            for item in root.findall('opf:manifest/opf:item', NS)
        }
        spine = root.findall('opf:spine/opf:itemref', NS)
        return [f"{base}/{manifest[ref.get('idref')]}" for ref in spine]

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```
Expected: 2 tests PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: epub metadata parsing and chapter path extraction"
```

---

### Task 3: Library endpoint

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Scans a `BOOKS_DIR` for `.epub` files, returns JSON list. Book `id` is the filename stem.

**Step 1: Write failing tests**

```python
import json, os, tempfile, pytest
from unittest.mock import patch

def test_library_returns_empty_list_when_no_books(client):
    with patch('server.BOOKS_DIR', tempfile.mkdtemp()):
        r = client.get('/library')
    assert r.status_code == 200
    assert json.loads(r.data) == []

def test_library_returns_book_metadata(client, tmp_path):
    epub_data = make_epub(title='三体', author='刘慈欣', language='zh', chapters=5)
    epub_file = tmp_path / 'three-body.epub'
    epub_file.write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/library')
    books = json.loads(r.data)
    assert len(books) == 1
    assert books[0]['id'] == 'three-body'
    assert books[0]['title'] == '三体'
    assert books[0]['author'] == '刘慈欣'
    assert books[0]['language'] == 'zh'
    assert books[0]['chapter_count'] == 5
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py::test_library_returns_empty_list_when_no_books -v
```
Expected: FAIL — 404

**Step 3: Implement**

Add to `server.py`, before `if __name__`:

```python
import os
from pathlib import Path
from flask import jsonify

BOOKS_DIR = os.environ.get('BOOKS_DIR', './books')

def _book_id(path: Path) -> str:
    return path.stem

@app.route('/library')
def library():
    books_path = Path(BOOKS_DIR)
    books = []
    for epub_path in sorted(books_path.glob('*.epub')):
        data = epub_path.read_bytes()
        try:
            meta = parse_epub_metadata(data)
            chapters = get_chapter_paths(data)
            books.append({
                'id': _book_id(epub_path),
                'title': meta['title'],
                'author': meta['author'],
                'language': meta['language'],
                'chapter_count': len(chapters),
                'cover_url': f'/book/{_book_id(epub_path)}/cover',
            })
        except Exception:
            continue  # skip malformed EPUBs
    return jsonify(books)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: library endpoint"
```

---

### Task 4: Cover endpoint

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Extracts cover image from EPUB manifest. Falls back to a blank 1x1 PNG if none found.

**Step 1: Write failing test**

```python
def test_cover_returns_image(client, tmp_path):
    epub_data = make_epub()
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/cover')
    # No cover in minimal epub — should still return 200 with a placeholder
    assert r.status_code == 200
    assert r.content_type.startswith('image/')

def test_cover_returns_404_for_unknown_book(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/nonexistent/cover')
    assert r.status_code == 404
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py::test_cover_returns_image -v
```
Expected: FAIL — 404

**Step 3: Implement**

```python
import base64
from flask import send_file, abort

# 1x1 grey PNG, base64 encoded
_PLACEHOLDER_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)

def get_cover_bytes(data: bytes) -> tuple[bytes, str] | None:
    """Return (image_bytes, media_type) or None."""
    with _open_epub(data) as z:
        root, base = _opf_root(z)
        for item in root.findall('opf:manifest/opf:item', NS):
            props = item.get('properties', '')
            media = item.get('media-type', '')
            if 'cover-image' in props or 'cover' in item.get('id', ''):
                if media.startswith('image/'):
                    href = f"{base}/{item.get('href')}"
                    try:
                        return z.read(href), media
                    except KeyError:
                        continue
    return None

@app.route('/book/<book_id>/cover')
def cover(book_id):
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    result = get_cover_bytes(epub_path.read_bytes())
    if result:
        img_bytes, media_type = result
        return send_file(io.BytesIO(img_bytes), mimetype=media_type)
    return send_file(io.BytesIO(_PLACEHOLDER_PNG), mimetype='image/png')
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: cover image endpoint with placeholder fallback"
```

---

### Task 5: CJK character wrapping

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

This is the core of the language-learning UX. Walks HTML text nodes, wraps CJK characters (Chinese) or space-delimited tokens (Korean) in `<span class="w">`.

**Step 1: Write failing tests**

```python
from server import wrap_cjk

def test_wrap_cjk_chinese_wraps_each_character():
    html = '<p>你好世界</p>'
    result = wrap_cjk(html, 'zh')
    assert result.count('<span class="w">') == 4
    assert '<span class="w">你</span>' in result
    assert '<span class="w">界</span>' in result

def test_wrap_cjk_does_not_wrap_punctuation():
    html = '<p>你好，世界。</p>'
    result = wrap_cjk(html, 'zh')
    # Only 4 CJK chars, punctuation not wrapped
    assert result.count('<span class="w">') == 4
    assert '，' in result  # punctuation preserved unwrapped

def test_wrap_cjk_korean_wraps_each_token():
    html = '<p>안녕 세계</p>'
    result = wrap_cjk(html, 'ko')
    assert '<span class="w">안녕</span>' in result
    assert '<span class="w">세계</span>' in result
    assert result.count('<span class="w">') == 2

def test_wrap_cjk_other_language_unchanged():
    html = '<p>Hello world</p>'
    result = wrap_cjk(html, 'en')
    assert result == html

def test_wrap_cjk_preserves_existing_tags():
    html = '<p><strong>你好</strong></p>'
    result = wrap_cjk(html, 'zh')
    assert '<strong>' in result
    assert result.count('<span class="w">') == 2
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "wrap_cjk" -v
```
Expected: all FAIL — ImportError

**Step 3: Implement**

```python
import re
from bs4 import BeautifulSoup, NavigableString

_CJK_RE = re.compile(
    r'[\u4e00-\u9fff'      # CJK Unified Ideographs
    r'\u3400-\u4dbf'       # CJK Extension A
    r'\U00020000-\U0002a6df'  # CJK Extension B
    r'\uac00-\ud7a3]'      # Hangul syllables (Korean)
)

def _wrap_text_node_zh(text: str) -> str:
    result = []
    for ch in text:
        if _CJK_RE.match(ch):
            result.append(f'<span class="w">{ch}</span>')
        else:
            result.append(ch)
    return ''.join(result)

def _wrap_text_node_ko(text: str) -> str:
    tokens = text.split(' ')
    wrapped = []
    for token in tokens:
        if token and _CJK_RE.search(token):
            wrapped.append(f'<span class="w">{token}</span>')
        else:
            wrapped.append(token)
    return ' '.join(wrapped)

def wrap_cjk(html: str, language: str) -> str:
    if language not in ('zh', 'ko'):
        return html
    soup = BeautifulSoup(html, 'lxml')
    wrap_fn = _wrap_text_node_zh if language == 'zh' else _wrap_text_node_ko
    for node in soup.find_all(string=True):
        if node.parent.name in ('script', 'style', 'span'):
            continue
        wrapped = wrap_fn(str(node))
        if wrapped != str(node):
            node.replace_with(BeautifulSoup(wrapped, 'lxml').body.decode_contents())
    return str(soup.body or soup)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "wrap_cjk" -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: CJK character/token wrapping for tap-to-lookup"
```

---

### Task 6: Chapter endpoint

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

Extracts chapter N from an EPUB, strips the `<head>`, applies CJK wrapping, returns an HTML fragment.

**Step 1: Write failing tests**

```python
def test_chapter_returns_html_fragment(client, tmp_path):
    epub_data = make_epub(chapters=3, language='zh')
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/0')
    assert r.status_code == 200
    assert r.content_type.startswith('text/html')
    html = r.data.decode()
    assert '<head>' not in html
    assert '<body>' not in html
    assert 'Chapter 0 content' in html

def test_chapter_wraps_cjk_for_chinese_book(client, tmp_path):
    epub_data = make_epub(chapters=1, language='zh')
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/0')
    html = r.data.decode()
    assert '<span class="w">' in html

def test_chapter_returns_404_for_bad_index(client, tmp_path):
    epub_data = make_epub(chapters=2)
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/99')
    assert r.status_code == 404
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "chapter" -v
```
Expected: FAIL — 404

**Step 3: Implement**

```python
from flask import Response

def extract_chapter(data: bytes, index: int) -> str:
    paths = get_chapter_paths(data)
    if index >= len(paths):
        return None
    with _open_epub(data) as z:
        raw = z.read(paths[index]).decode('utf-8', errors='replace')
    soup = BeautifulSoup(raw, 'lxml')
    body = soup.find('body')
    content = body.decode_contents() if body else raw
    return content

@app.route('/book/<book_id>/chapter/<int:index>')
def chapter(book_id, index):
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    data = epub_path.read_bytes()
    content = extract_chapter(data, index)
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
git commit -m "feat: chapter extraction endpoint"
```

---

### Task 7: Frontend — static serving + HTML shell

**Files:**
- Modify: `server.py` (catch-all route)
- Create: `static/index.html`
- Create: `static/style.css`
- Create: `static/app.js`

**Step 1: Add catch-all route to `server.py`**

```python
from flask import send_from_directory

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')
```

**Step 2: Create `static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>Reader</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="library-view" class="view active">
    <header class="topbar">
      <span class="topbar-title">Library</span>
    </header>
    <div id="book-grid"></div>
  </div>

  <div id="reader-view" class="view">
    <header class="topbar">
      <button id="back-btn">←</button>
      <span id="book-title" class="topbar-title"></span>
      <span id="page-indicator"></span>
    </header>
    <div id="chapter-container"></div>
    <div id="lookup-popup" class="popup hidden">
      <div class="popup-header">
        <span id="popup-word"></span>
        <button id="popup-close">✕</button>
      </div>
      <div id="popup-pronunciation"></div>
      <div id="popup-definitions"></div>
      <a id="popup-wiktionary" target="_blank">Open in Wiktionary ↗</a>
    </div>
    <div id="lookup-pill" class="hidden">Look up</div>
  </div>

  <script src="/static/app.js"></script>
</body>
</html>
```

**Step 3: Create `static/style.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #f8f4ef;
  --fg: #1a1a1a;
  --accent: #5c7a5c;
  --bar-h: 48px;
  --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

html, body { height: 100%; background: var(--bg); color: var(--fg); font-family: var(--sans); overflow: hidden; }

.view { display: none; height: 100vh; flex-direction: column; }
.view.active { display: flex; }

/* Topbar */
.topbar { height: var(--bar-h); display: flex; align-items: center; padding: 0 12px; gap: 10px; background: var(--bg); border-bottom: 1px solid #ddd; flex-shrink: 0; }
.topbar-title { flex: 1; font-weight: 600; font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#back-btn { background: none; border: none; font-size: 1.3rem; cursor: pointer; padding: 8px; }
#page-indicator { font-size: 0.8rem; color: #888; white-space: nowrap; }

/* Library */
#book-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 16px; padding: 16px; overflow-y: auto; }
.book-card { cursor: pointer; display: flex; flex-direction: column; gap: 6px; }
.book-card img { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,.15); }
.book-card .book-title { font-size: 0.8rem; font-weight: 500; line-height: 1.3; }
.book-card .book-author { font-size: 0.75rem; color: #888; }

/* Reader */
#chapter-container { flex: 1; overflow: hidden; position: relative; }
.chapter-content { column-width: 100vw; column-gap: 0; height: calc(100vh - var(--bar-h)); overflow: hidden; transition: transform 0.2s ease; padding: 24px 28px; line-height: 1.8; font-size: 1.05rem; will-change: transform; }
.chapter-content * { max-width: 100%; }
span.w { cursor: pointer; border-radius: 2px; transition: background 0.1s; }
span.w:hover { background: rgba(92,122,92,0.15); }
span.w.active { background: rgba(92,122,92,0.3); }

/* Lookup popup — bottom sheet */
.popup { position: fixed; bottom: 0; left: 0; right: 0; background: white; border-radius: 16px 16px 0 0; box-shadow: 0 -4px 20px rgba(0,0,0,.15); padding: 20px; z-index: 100; transition: transform 0.25s ease; max-height: 60vh; overflow-y: auto; }
.popup.hidden { display: none; }
.popup-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
#popup-word { font-size: 1.8rem; font-weight: 700; }
#popup-close { background: none; border: none; font-size: 1.2rem; cursor: pointer; padding: 4px 8px; }
#popup-pronunciation { font-size: 1rem; color: var(--accent); margin-bottom: 10px; }
#popup-definitions { font-size: 0.9rem; line-height: 1.7; }
#popup-definitions li { margin-left: 18px; margin-bottom: 4px; }
#popup-wiktionary { display: block; margin-top: 14px; font-size: 0.85rem; color: var(--accent); text-decoration: none; }

/* Selection pill */
#lookup-pill { position: fixed; background: var(--fg); color: white; padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; cursor: pointer; z-index: 99; display: none; }
#lookup-pill.visible { display: block; }
```

**Step 4: Create empty `static/app.js`**

```js
// app.js — implemented in subsequent tasks
```

**Step 5: Verify server starts and serves the shell**

```bash
mkdir -p books
python server.py
# open http://localhost:8090 — should see the library shell with empty grid
```

**Step 6: Commit**

```bash
git add static/ server.py
git commit -m "feat: static frontend shell and styles"
```

---

### Task 8: Frontend — library view

**Files:**
- Modify: `static/app.js`

**Step 1: Implement library JS**

```js
// ── State ────────────────────────────────────────────────────────────────────
const state = {
  books: [],
  current: null,   // { id, title, language, chapterCount }
  chapter: 0,
  page: 0,
  cache: new Map(), // chapterIndex → html string
};

// ── Views ────────────────────────────────────────────────────────────────────
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Library ──────────────────────────────────────────────────────────────────
async function loadLibrary() {
  const res = await fetch('/library');
  state.books = await res.json();
  renderLibrary();
}

function renderLibrary() {
  const grid = document.getElementById('book-grid');
  grid.innerHTML = '';
  if (state.books.length === 0) {
    grid.innerHTML = '<p style="padding:16px;color:#888">No books found. Add EPUBs to the books/ directory.</p>';
    return;
  }
  state.books.forEach(book => {
    const card = document.createElement('div');
    card.className = 'book-card';
    card.innerHTML = `
      <img src="${book.cover_url}" alt="" loading="lazy" onerror="this.style.background='#ddd'">
      <div class="book-title">${book.title}</div>
      <div class="book-author">${book.author}</div>
    `;
    card.addEventListener('click', () => openBook(book));
    grid.appendChild(card);
  });
}

loadLibrary();
```

**Step 2: Verify in browser**

```bash
python server.py
# Add a test epub to books/, reload — should see cover grid
```

**Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: library view with book grid"
```

---

### Task 9: Frontend — reader and pagination

**Files:**
- Modify: `static/app.js`

**Step 1: Append reader code to `app.js`**

```js
// ── Reader ───────────────────────────────────────────────────────────────────
async function openBook(book) {
  state.current = book;
  state.chapter = 0;
  state.page = 0;
  state.cache.clear();
  document.getElementById('book-title').textContent = book.title;
  showView('reader-view');
  await loadChapter(0);
  prefetchAhead(0);
}

async function fetchChapter(index) {
  if (state.cache.has(index)) return state.cache.get(index);
  const res = await fetch(`/book/${state.current.id}/chapter/${index}`);
  if (!res.ok) return null;
  const html = await res.text();
  state.cache.set(index, html);
  evictCache(index);
  return html;
}

function evictCache(current) {
  for (const key of state.cache.keys()) {
    if (key < current - 2) state.cache.delete(key);
  }
}

function prefetchAhead(from) {
  const total = state.current.chapterCount;
  for (let i = from + 1; i <= Math.min(from + 5, total - 1); i++) {
    fetchChapter(i); // fire and forget
  }
}

async function loadChapter(index) {
  const html = await fetchChapter(index);
  if (html === null) return;
  state.chapter = index;
  state.page = 0;

  const container = document.getElementById('chapter-container');
  container.innerHTML = '';

  const content = document.createElement('div');
  content.className = 'chapter-content';
  content.innerHTML = html;
  container.appendChild(content);

  // Wait for layout, then calculate page count
  requestAnimationFrame(() => {
    updatePageIndicator(content);
    setPage(content, 0);
  });
}

function pageCount(content) {
  return Math.max(1, Math.round(content.scrollWidth / window.innerWidth));
}

function setPage(content, page) {
  const total = pageCount(content);
  state.page = Math.max(0, Math.min(page, total - 1));
  content.style.transform = `translateX(${-state.page * window.innerWidth}px)`;
  updatePageIndicator(content);
}

function updatePageIndicator(content) {
  const el = document.getElementById('page-indicator');
  const total = pageCount(content);
  const ch = state.chapter + 1;
  const chTotal = state.current?.chapterCount ?? '?';
  el.textContent = `Ch ${ch}/${chTotal}  ·  ${state.page + 1}/${total}`;
}

function currentContent() {
  return document.querySelector('.chapter-content');
}

// ── Navigation ───────────────────────────────────────────────────────────────
async function nextPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page < pageCount(content) - 1) {
    setPage(content, state.page + 1);
  } else if (state.chapter < state.current.chapterCount - 1) {
    await loadChapter(state.chapter + 1);
    prefetchAhead(state.chapter);
  }
}

async function prevPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page > 0) {
    setPage(content, state.page - 1);
  } else if (state.chapter > 0) {
    await loadChapter(state.chapter - 1);
    // Jump to last page of previous chapter
    const c = currentContent();
    if (c) setPage(c, pageCount(c) - 1);
  }
}

// ── Touch / keyboard input ───────────────────────────────────────────────────
let touchStartX = 0;
document.getElementById('reader-view').addEventListener('touchstart', e => {
  touchStartX = e.changedTouches[0].clientX;
}, { passive: true });

document.getElementById('reader-view').addEventListener('touchend', e => {
  const dx = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(dx) > 40) dx < 0 ? nextPage() : prevPage();
});

document.addEventListener('keydown', e => {
  if (!document.getElementById('reader-view').classList.contains('active')) return;
  if (e.key === 'ArrowRight') nextPage();
  if (e.key === 'ArrowLeft') prevPage();
});

// ── Back button ──────────────────────────────────────────────────────────────
document.getElementById('back-btn').addEventListener('click', () => {
  showView('library-view');
});
```

**Step 2: Wire `openBook` in the library card click handler**

The `openBook` function is already referenced in Task 8's card click handler — confirm it's accessible (same file scope, no modules needed).

**Step 3: Verify in browser**

```bash
python server.py
# Open a book, swipe/arrow to paginate — chapters should load instantly from cache after prefetch
```

**Step 4: Commit**

```bash
git add static/app.js
git commit -m "feat: reader view with CSS multi-column pagination and chapter lookahead"
```

---

### Task 10: Frontend — dictionary popup

**Files:**
- Modify: `static/app.js`

**Step 1: Append dictionary code to `app.js`**

```js
// ── Dictionary ───────────────────────────────────────────────────────────────
async function lookupWord(word) {
  showPopup(word, null, null); // show immediately with loading state

  const url = `https://en.wiktionary.org/w/api.php?action=parse&page=${encodeURIComponent(word)}&prop=text&format=json&origin=*`;
  let pronunciation = '';
  let definitions = [];

  try {
    const res = await fetch(url);
    const data = await res.json();
    if (data.error) throw new Error('not found');

    const html = data.parse.text['*'];
    const doc = new DOMParser().parseFromString(html, 'text/html');

    // Find the relevant language section
    const lang = state.current?.language ?? '';
    const langMap = { zh: 'Chinese', ko: 'Korean' };
    const targetLang = langMap[lang] ?? '';

    // Find language heading
    let langSection = null;
    doc.querySelectorAll('h2').forEach(h2 => {
      if (h2.textContent.includes(targetLang)) langSection = h2;
    });

    const root = langSection ? langSection.parentElement : doc.body;

    // Extract pronunciation (IPA or pinyin)
    const pronEl = root.querySelector('.IPA, .pinyin, [class*="pron"]');
    pronunciation = pronEl?.textContent?.trim() ?? '';

    // Extract definitions from ordered lists
    root.querySelectorAll('ol li').forEach(li => {
      const text = li.childNodes[0]?.textContent?.trim();
      if (text && text.length > 1) definitions.push(text);
    });

    if (!pronunciation && !definitions.length) throw new Error('no content');
  } catch {
    definitions = ['No Wiktionary entry found.'];
  }

  showPopup(word, pronunciation, definitions);
}

function showPopup(word, pronunciation, definitions) {
  document.getElementById('popup-word').textContent = word;
  document.getElementById('popup-pronunciation').textContent = pronunciation ?? 'Loading…';
  const defEl = document.getElementById('popup-definitions');
  if (definitions === null) {
    defEl.innerHTML = '<em>Loading…</em>';
  } else {
    defEl.innerHTML = definitions.length
      ? '<ol>' + definitions.slice(0, 5).map(d => `<li>${d}</li>`).join('') + '</ol>'
      : '';
  }
  const wikiLink = document.getElementById('popup-wiktionary');
  wikiLink.href = `https://en.wiktionary.org/wiki/${encodeURIComponent(word)}`;
  document.getElementById('lookup-popup').classList.remove('hidden');
}

function hidePopup() {
  document.getElementById('lookup-popup').classList.add('hidden');
  document.querySelectorAll('span.w.active').forEach(s => s.classList.remove('active'));
}

document.getElementById('popup-close').addEventListener('click', hidePopup);
document.getElementById('chapter-container').addEventListener('click', e => {
  if (e.target.classList.contains('w')) {
    document.querySelectorAll('span.w.active').forEach(s => s.classList.remove('active'));
    e.target.classList.add('active');
    lookupWord(e.target.textContent);
    e.stopPropagation();
    return;
  }
  hidePopup();
});

// ── Text selection pill ───────────────────────────────────────────────────────
const pill = document.getElementById('lookup-pill');
document.addEventListener('selectionchange', () => {
  const sel = window.getSelection();
  const text = sel?.toString().trim();
  if (!text || text.length < 1) { pill.style.display = 'none'; return; }
  const range = sel.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  pill.style.display = 'block';
  pill.style.left = `${rect.left + rect.width / 2 - 40}px`;
  pill.style.top = `${rect.top - 44 + window.scrollY}px`;
  pill.classList.add('visible');
});

pill.addEventListener('click', () => {
  const text = window.getSelection()?.toString().trim();
  if (text) lookupWord(text);
  pill.classList.remove('visible');
});
```

**Step 2: Verify end-to-end in browser**

```bash
python server.py
# Open a Chinese book, tap a character — bottom sheet should appear with pronunciation and definition
# Select multiple characters — pill should appear, tap to look up
```

**Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: tap-to-lookup dictionary popup via Wiktionary API"
```

---

### Task 11: Run full test suite and tidy up

**Step 1: Run all tests**

```bash
pytest tests/ -v
```
Expected: all PASS

**Step 2: Smoke test with a real EPUB**

```bash
# Put any epub in books/ and run:
python server.py
# Verify: library lists it, cover loads, chapters paginate, tap-to-lookup works
```

**Step 3: Final commit**

```bash
git add .
git commit -m "chore: verified full flow with real epub"
```
