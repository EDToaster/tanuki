import zipfile, io, json, pytest
from unittest.mock import patch
from server import parse_epub_metadata, get_chapter_paths, _validate_book_id


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
