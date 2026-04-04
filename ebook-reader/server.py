import zipfile, io, os, re, base64
import xml.etree.ElementTree as ET
from pathlib import Path
from flask import Flask, jsonify, Response, send_file, send_from_directory, abort
from bs4 import BeautifulSoup, NavigableString

app = Flask(__name__, static_folder='static')
BOOKS_DIR = os.environ.get('BOOKS_DIR', './books')

NS = {
    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
}

# ── Security ──────────────────────────────────────────────────────────────────

_SAFE_BOOK_ID = re.compile(r'^[a-z0-9_-]+$')


def _validate_book_id(book_id: str):
    if not _SAFE_BOOK_ID.match(book_id):
        abort(400)


# ── EPUB parsing helpers ──────────────────────────────────────────────────────

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


# ── Library ───────────────────────────────────────────────────────────────────

@app.route('/library')
def library():
    books_path = Path(BOOKS_DIR)
    books = []
    for epub_path in sorted(books_path.glob('*.epub')):
        data = epub_path.read_bytes()
        try:
            meta = parse_epub_metadata(data)
            chapters = get_chapter_paths(data)
            book_id = epub_path.stem
            books.append({
                'id': book_id,
                'title': meta['title'],
                'author': meta['author'],
                'language': meta['language'],
                'chapter_count': len(chapters),
                'cover_url': f'/book/{book_id}/cover',
            })
        except Exception:
            continue
    return jsonify(books)


# ── Cover ─────────────────────────────────────────────────────────────────────

_PLACEHOLDER_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)


def get_cover_bytes(data: bytes) -> tuple[bytes, str] | None:
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
    _validate_book_id(book_id)
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    result = get_cover_bytes(epub_path.read_bytes())
    if result:
        img_bytes, media_type = result
        return send_file(io.BytesIO(img_bytes), mimetype=media_type)
    return send_file(io.BytesIO(_PLACEHOLDER_PNG), mimetype='image/png')


# ── XSS sanitization + CJK wrapping ──────────────────────────────────────────

_CJK_RE = re.compile(
    r'[\u4e00-\u9fff'
    r'\u3400-\u4dbf'
    r'\U00020000-\U0002a6df'
    r'\uac00-\ud7a3]'
)


def _sanitize_html(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(['script', 'style']):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.startswith('on'):
                del tag[attr]
            elif attr == 'href' and str(tag.get('href', '')).strip().lower().startswith('javascript:'):
                del tag[attr]
            elif attr == 'src' and str(tag.get('src', '')).strip().lower().startswith('javascript:'):
                del tag[attr]


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


def extract_chapter(data: bytes, index: int) -> str | None:
    paths = get_chapter_paths(data)
    if index >= len(paths):
        return None
    with _open_epub(data) as z:
        raw = z.read(paths[index]).decode('utf-8', errors='replace')
    soup = BeautifulSoup(raw, 'lxml')
    _sanitize_html(soup)
    body = soup.find('body')
    content = body.decode_contents() if body else raw
    if not content.strip():
        content = '<p class="chapter-empty">Chapter has no readable content.</p>'
    return content


# ── Chapter + static ──────────────────────────────────────────────────────────

@app.route('/book/<book_id>/chapter/<int:index>')
def chapter(book_id, index):
    _validate_book_id(book_id)
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    try:
        data = epub_path.read_bytes()
        content = extract_chapter(data, index)
    except Exception:
        abort(500)
    if content is None:
        abort(404)
    meta = parse_epub_metadata(data)
    wrapped = wrap_cjk(content, meta['language'])
    return Response(wrapped, mimetype='text/html; charset=utf-8')


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
