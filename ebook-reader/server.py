import zipfile, io, os, re, base64, sqlite3, datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from flask import Flask, jsonify, Response, send_file, send_from_directory, abort, request
from bs4 import BeautifulSoup, NavigableString

app = Flask(__name__, static_folder='static')
BOOKS_DIR = os.environ.get('BOOKS_DIR', './books')
DB_PATH = os.environ.get('DB_PATH', '/data/progress.db')

# ── SQLite schema + helpers ───────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    UNIQUE NOT NULL COLLATE NOCASE,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS progress (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    book_id    TEXT    NOT NULL,
    chapter_id INTEGER NOT NULL,
    page_index INTEGER NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (profile_id, book_id)
);

CREATE INDEX IF NOT EXISTS idx_progress_profile      ON progress (profile_id);
CREATE INDEX IF NOT EXISTS idx_progress_profile_book ON progress (profile_id, book_id);
"""


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(_SCHEMA)
    con.close()


def get_db():
    con = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


_PROFILE_NAME_RE = re.compile(r'^[a-zA-Z0-9_-]{1,32}$')


def validate_profile_name(name: str) -> bool:
    return bool(_PROFILE_NAME_RE.match(name))


def _get_or_create_profile_id(con, name: str):
    if not validate_profile_name(name):
        return None
    row = con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row['id']
    con.execute('INSERT INTO profiles (name) VALUES (?)', (name,))
    return con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()['id']


def _require_profile_id(con, name: str) -> int:
    row = con.execute('SELECT id FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if not row:
        abort(404)
    return row['id']


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
            new_soup = BeautifulSoup(wrapped, 'lxml')
            new_nodes = list(new_soup.body.children)
            node.replace_with(*[n.extract() for n in new_nodes])
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


# ── Profile endpoints ─────────────────────────────────────────────────────────

@app.route('/api/profiles', methods=['GET'])
def list_profiles():
    with get_db() as con:
        rows = con.execute('SELECT name, created_at FROM profiles ORDER BY created_at').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/profiles', methods=['POST'])
def create_profile():
    body = request.get_json(silent=True) or {}
    name = body.get('name', '')
    if not validate_profile_name(name):
        return jsonify({'error': 'name must be 1–32 chars, letters/digits/hyphens/underscores only'}), 400
    try:
        with get_db() as con:
            con.execute('INSERT INTO profiles (name) VALUES (?)', (name,))
            row = con.execute('SELECT name, created_at FROM profiles WHERE name=? COLLATE NOCASE', (name,)).fetchone()
        return jsonify(dict(row)), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': f"profile '{name}' already exists"}), 409


@app.route('/api/profiles/<name>', methods=['DELETE'])
def delete_profile(name):
    with get_db() as con:
        cur = con.execute('DELETE FROM profiles WHERE name=? COLLATE NOCASE', (name,))
    if cur.rowcount == 0:
        abort(404)
    return Response(status=204)


# ── Progress endpoints ────────────────────────────────────────────────────────

@app.route('/api/u/<name>/progress', methods=['GET'])
def get_all_progress(name):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        rows = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=?',
            (profile_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/u/<name>/progress/<book_id>', methods=['GET'])
def get_progress(name, book_id):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        row = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=? AND book_id=?',
            (profile_id, book_id)
        ).fetchone()
    if not row:
        abort(404)
    return jsonify(dict(row))


@app.route('/api/u/<name>/progress/<book_id>', methods=['PUT'])
def put_progress(name, book_id):
    body = request.get_json(silent=True) or {}
    chapter_id = body.get('chapter_id')
    page_index  = body.get('page_index')
    if not isinstance(chapter_id, int) or not isinstance(page_index, int) or chapter_id < 0 or page_index < 0:
        return jsonify({'error': 'chapter_id and page_index must be non-negative integers'}), 400
    with get_db() as con:
        profile_id = _get_or_create_profile_id(con, name)
        if profile_id is None:
            abort(400)
        now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        con.execute(
            '''INSERT INTO progress (profile_id, book_id, chapter_id, page_index, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, book_id) DO UPDATE SET
                   chapter_id=excluded.chapter_id,
                   page_index=excluded.page_index,
                   updated_at=excluded.updated_at''',
            (profile_id, book_id, chapter_id, page_index, now)
        )
        row = con.execute(
            'SELECT book_id, chapter_id, page_index, updated_at FROM progress WHERE profile_id=? AND book_id=?',
            (profile_id, book_id)
        ).fetchone()
    return jsonify(dict(row))


@app.route('/api/u/<name>/progress/<book_id>', methods=['DELETE'])
def delete_progress(name, book_id):
    with get_db() as con:
        profile_id = _require_profile_id(con, name)
        con.execute('DELETE FROM progress WHERE profile_id=? AND book_id=?', (profile_id, book_id))
    return Response(status=204)


# ── SPA routing ───────────────────────────────────────────────────────────────

@app.route('/')
@app.route('/u/<name>/')
@app.route('/u/<name>/book/<book_id>')
def spa(name=None, book_id=None):
    return send_from_directory(app.static_folder, 'index.html')


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8090, debug=True)
