import zipfile, io, json, pytest
from unittest.mock import patch
from server import parse_epub_metadata, get_chapter_paths, _validate_book_id, validate_profile_name


# ── EPUB fixture factory ──────────────────────────────────────────────────────

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


# ── EPUB parsing ──────────────────────────────────────────────────────────────

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
    assert all(isinstance(p, str) for p in paths)


# ── Path traversal validation ─────────────────────────────────────────────────

def test_validate_book_id_accepts_valid_ids():
    _validate_book_id('my-book')
    _validate_book_id('three_body_problem')
    _validate_book_id('book123')

def test_validate_book_id_rejects_path_traversal(client):
    r = client.get('/book/../etc/passwd/cover')
    assert r.status_code in (400, 404)

def test_validate_book_id_rejects_dots(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/../../etc/cover')
    assert r.status_code in (400, 404)


# ── Library endpoint ──────────────────────────────────────────────────────────

def test_library_returns_empty_list_when_no_books(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
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


# ── Cover endpoint ────────────────────────────────────────────────────────────

def test_cover_returns_image(client, tmp_path):
    epub_data = make_epub()
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/cover')
    assert r.status_code == 200
    assert r.content_type.startswith('image/')

def test_cover_returns_404_for_unknown_book(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/nonexistent/cover')
    assert r.status_code == 404

def test_cover_rejects_invalid_book_id(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/../secrets/cover')
    assert r.status_code in (400, 404)


# ── Chapter endpoint ──────────────────────────────────────────────────────────

def test_chapter_returns_html_fragment(client, tmp_path):
    epub_data = make_epub(chapters=3, language='zh')
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/0')
    assert r.status_code == 200
    assert r.content_type.startswith('text/html')
    html = r.data.decode()
    assert '<head>' not in html
    assert 'Chapter 0 content' in html

def test_chapter_strips_script_tags(client, tmp_path):
    """XSS fix #1: server must strip <script> and on* attrs from chapter HTML."""
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
  <manifest><item id="c0" href="chapter0.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c0"/></spine>
</package>''')
        z.writestr('OEBPS/chapter0.xhtml',
            '<html><body><p>Hello</p><script>alert(1)</script>'
            '<p onclick="evil()">World</p></body></html>')
    buf.seek(0)
    (tmp_path / 'xssbook.epub').write_bytes(buf.read())
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/xssbook/chapter/0')
    html = r.data.decode()
    assert '<script>' not in html
    assert 'onclick' not in html

def test_chapter_returns_404_for_bad_index(client, tmp_path):
    epub_data = make_epub(chapters=2)
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/99')
    assert r.status_code == 404

def test_chapter_rejects_invalid_book_id(client, tmp_path):
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/../etc/chapter/0')
    assert r.status_code in (400, 404)

def test_chapter_wraps_cjk_for_chinese(client, tmp_path):
    epub_data = make_epub(chapters=1, language='zh')
    (tmp_path / 'mybook.epub').write_bytes(epub_data)
    with patch('server.BOOKS_DIR', str(tmp_path)):
        r = client.get('/book/mybook/chapter/0')
    assert '<span class="w">' in r.data.decode()


# ── Phase 5: DB init ──────────────────────────────────────────────────────────

def test_init_db_creates_tables(tmp_path, monkeypatch):
    import server, sqlite3
    db_path = str(tmp_path / 'progress.db')
    monkeypatch.setattr(server, 'DB_PATH', db_path)
    server.init_db()
    con = sqlite3.connect(db_path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert 'profiles' in tables
    assert 'progress' in tables
    con.close()

def test_init_db_idempotent(tmp_path, monkeypatch):
    import server
    db_path = str(tmp_path / 'progress.db')
    monkeypatch.setattr(server, 'DB_PATH', db_path)
    server.init_db()
    server.init_db()  # must not raise


# ── Phase 5: Profile name validation ─────────────────────────────────────────

def test_valid_profile_names():
    for name in ['howard', 'Alice', 'user-1', 'user_2', 'A' * 32]:
        assert validate_profile_name(name) is True

def test_invalid_profile_names():
    for name in ['', 'a' * 33, 'has space', 'has/slash', 'has.dot', '<script>']:
        assert validate_profile_name(name) is False


# ── Phase 5: Profile endpoints ────────────────────────────────────────────────

def test_list_profiles_empty(client):
    r = client.get('/api/profiles')
    assert r.status_code == 200
    assert r.get_json() == []

def test_create_profile(client):
    r = client.post('/api/profiles', json={'name': 'howard'})
    assert r.status_code == 201
    data = r.get_json()
    assert data['name'] == 'howard'
    assert 'created_at' in data

def test_create_profile_duplicate_409(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.post('/api/profiles', json={'name': 'howard'})
    assert r.status_code == 409

def test_create_profile_invalid_name_400(client):
    r = client.post('/api/profiles', json={'name': 'bad name!'})
    assert r.status_code == 400

def test_delete_profile(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.delete('/api/profiles/howard')
    assert r.status_code == 204

def test_delete_profile_not_found(client):
    r = client.delete('/api/profiles/nobody')
    assert r.status_code == 404


# ── Phase 5: Progress endpoints ───────────────────────────────────────────────

def test_get_all_progress_empty(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.get('/api/u/howard/progress')
    assert r.status_code == 200
    assert r.get_json() == []

def test_get_all_progress_unknown_profile(client):
    r = client.get('/api/u/nobody/progress')
    assert r.status_code == 404

def test_put_and_get_progress(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.put('/api/u/howard/progress/three-body',
                   json={'chapter_id': 4, 'page_index': 2})
    assert r.status_code == 200
    data = r.get_json()
    assert data['chapter_id'] == 4
    assert data['page_index'] == 2

def test_put_progress_upserts(client):
    client.post('/api/profiles', json={'name': 'howard'})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 4, 'page_index': 2})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 5, 'page_index': 0})
    r = client.get('/api/u/howard/progress/three-body')
    assert r.get_json()['chapter_id'] == 5

def test_put_progress_autocreates_profile(client):
    r = client.put('/api/u/newuser/progress/three-body',
                   json={'chapter_id': 1, 'page_index': 0})
    assert r.status_code == 200

def test_delete_progress(client):
    client.post('/api/profiles', json={'name': 'howard'})
    client.put('/api/u/howard/progress/three-body', json={'chapter_id': 4, 'page_index': 2})
    r = client.delete('/api/u/howard/progress/three-body')
    assert r.status_code == 204
    r2 = client.get('/api/u/howard/progress/three-body')
    assert r2.status_code == 404

def test_put_progress_invalid_body(client):
    client.post('/api/profiles', json={'name': 'howard'})
    r = client.put('/api/u/howard/progress/three-body', json={'chapter_id': 'bad'})
    assert r.status_code == 400
