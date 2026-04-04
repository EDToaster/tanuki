import zipfile, io, json, pytest
from unittest.mock import patch
from server import (
    parse_epub_metadata, get_chapter_paths, _validate_book_id,
    validate_profile_name, normalize_html,
    get_segmenter, JiebaSegmenter, KoreanJosaSegmenter, WhitespaceSegmenter,
    wrap_with_segmenter,
)


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
    # Chapter content has 你好 which jieba segments as a 2-char compound
    assert '<span class="w"' in r.data.decode()


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


# ── normalize_html unit tests ─────────────────────────────────────────────────

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


# ── Phase 3: dictionary ───────────────────────────────────────────────────────

from unittest.mock import MagicMock
from server import (
    normalize_dict_response, not_found_response,
    WiktionaryProvider, NIKLProvider, DictProvider, PROVIDER_CHAINS,
)
import urllib.request


# ── normalize_dict_response ───────────────────────────────────────────────────

def test_normalized_response_has_required_fields():
    entry = {
        'word': '学校',
        'readings': [{'text': 'xuéxiào', 'romanization': 'xuéxiào'}],
        'definitions': [{'pos': 'noun', 'text': 'school'}],
        'source': 'wiktionary',
        'source_url': 'https://en.wiktionary.org/wiki/学校',
        'not_found': False,
    }
    result = normalize_dict_response(entry)
    assert result['word'] == '学校'
    assert result['not_found'] is False
    assert isinstance(result['definitions'], list)

def test_not_found_response():
    result = not_found_response('nonexistentword')
    assert result['not_found'] is True
    assert result['word'] == 'nonexistentword'
    assert result['definitions'] == []


# ── WiktionaryProvider ────────────────────────────────────────────────────────

MOCK_WIKTIONARY_HTML = '''
<div>
<h2><span class="mw-headline" id="Chinese">Chinese</span></h2>
<div class="mw-parser-output">
<span class="IPA">/xuě/</span>
<ol><li>snow</li><li>to snow</li></ol>
</div>
</div>
'''

def test_wiktionary_provider_parses_definitions():
    mock_response_data = json.dumps({
        'parse': {'text': {'*': MOCK_WIKTIONARY_HTML}}
    }).encode()
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = lambda: mock_response_data
        provider = WiktionaryProvider()
        result = provider.lookup('雪', lang='zh')
    assert result is not None
    assert result['not_found'] is False
    assert len(result['definitions']) > 0

def test_wiktionary_provider_returns_none_on_error():
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.side_effect = Exception('network error')
        provider = WiktionaryProvider()
        result = provider.lookup('雪', lang='zh')
    assert result is None


# ── NIKLProvider ──────────────────────────────────────────────────────────────

MOCK_NIKL_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <total>1</total>
  <item>
    <target_code>26655</target_code>
    <word>나무</word>
    <pronunciation>나무</pronunciation>
    <pos>명사</pos>
    <word_grade>초급</word_grade>
    <sense>
      <sense_order>1</sense_order>
      <definition>단단한 줄기에 가지와 잎이 달린 식물.</definition>
      <translation>
        <trans_lang>1</trans_lang>
        <trans_word>tree</trans_word>
        <trans_dfn>A plant with a firm stem, branches, and leaves.</trans_dfn>
      </translation>
    </sense>
    <link>https://krdict.korean.go.kr/dicSearch/search?mainSearchWord=나무</link>
  </item>
</channel>'''.encode('utf-8')

def test_nikl_provider_parses_xml():
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = lambda: MOCK_NIKL_XML
        provider = NIKLProvider(api_key='test-key-32chars-padding0000000')
        result = provider.lookup('나무', lang='ko')
    assert result is not None
    assert result['word'] == '나무'
    assert result['not_found'] is False
    assert any('tree' in d['text'] for d in result['definitions'])

def test_nikl_provider_returns_none_without_api_key():
    provider = NIKLProvider(api_key=None)
    result = provider.lookup('나무', lang='ko')
    assert result is None

def test_nikl_provider_caches_results():
    call_count = 0

    def counting_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        m.read = lambda: MOCK_NIKL_XML
        return m

    provider = NIKLProvider(api_key='test-key-32chars-padding0000000')
    provider._cache.clear()

    with patch('urllib.request.urlopen', side_effect=counting_urlopen):
        provider.lookup('나무', lang='ko')
        provider.lookup('나무', lang='ko')

    assert call_count == 1


# ── /api/dict endpoint ────────────────────────────────────────────────────────

def test_api_dict_endpoint_returns_json(client):
    mock_result = {
        'word': '中', 'readings': [], 'definitions': [{'pos': 'noun', 'text': 'test'}],
        'source': 'mock', 'source_url': None, 'not_found': False,
    }
    with patch('server._lookup_word', return_value=mock_result):
        r = client.get('/api/dict?word=%E4%B8%AD&lang=zh')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['word'] == '中'

def test_api_dict_endpoint_returns_not_found(client):
    with patch('server._lookup_word', return_value=not_found_response('zzz')):
        r = client.get('/api/dict?word=zzz&lang=zh')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['not_found'] is True

def test_api_dict_endpoint_requires_word_param(client):
    r = client.get('/api/dict?lang=zh')
    assert r.status_code == 400


# ── Segmenter interface ────────────────────────────────────────────────────────

def test_get_segmenter_zh_returns_jieba():
    assert isinstance(get_segmenter('zh'), JiebaSegmenter)

def test_get_segmenter_zh_tw_returns_jieba():
    assert isinstance(get_segmenter('zh-TW'), JiebaSegmenter)

def test_get_segmenter_ko_returns_korean():
    assert isinstance(get_segmenter('ko'), KoreanJosaSegmenter)

def test_get_segmenter_unknown_returns_whitespace():
    assert isinstance(get_segmenter('fr'), WhitespaceSegmenter)

def test_jieba_segmenter_returns_word_pairs():
    seg = JiebaSegmenter()
    tokens = seg.segment('中文')
    words = [w for w, _ in tokens]
    assert '中文' in words

def test_jieba_segmenter_non_cjk_returns_none_lookup():
    seg = JiebaSegmenter()
    tokens = seg.segment('hello')
    for word, lookup in tokens:
        if word == 'hello':
            assert lookup is None

def test_jieba_segmenter_cjk_sets_lookup():
    seg = JiebaSegmenter()
    tokens = seg.segment('电话')
    assert any(w == '电话' for w, _ in tokens)

def test_korean_josa_segmenter_strips_particle():
    seg = KoreanJosaSegmenter()
    tokens = seg.segment('학교에서')
    assert len(tokens) == 1
    word, lookup = tokens[0]
    assert word == '학교에서'
    assert lookup == '학교'

def test_korean_josa_segmenter_bare_noun_lookup_equals_word():
    seg = KoreanJosaSegmenter()
    tokens = seg.segment('학교')
    word, lookup = tokens[0]
    assert word == '학교'
    assert lookup == '학교'

def test_whitespace_segmenter_splits_on_spaces():
    seg = WhitespaceSegmenter()
    tokens = seg.segment('hello world')
    assert ('hello', 'hello') in tokens
    assert ('world', 'world') in tokens


# ── wrap_with_segmenter ────────────────────────────────────────────────────────

def test_wrap_chinese_uses_jieba():
    result = wrap_with_segmenter('<p>中文学习</p>', 'zh')
    assert '<span class="w"' in result
    assert '<span class="w">中</span><span class="w">文</span>' not in result

def test_wrap_chinese_multi_char_has_data_lookup():
    result = wrap_with_segmenter('<p>电话</p>', 'zh')
    assert 'data-lookup="电话"' in result

def test_wrap_chinese_single_char_no_data_lookup():
    result = wrap_with_segmenter('<p>我</p>', 'zh')
    assert '<span class="w">我</span>' in result
    assert 'data-lookup="我"' not in result

def test_wrap_korean_strips_josa():
    result = wrap_with_segmenter('<p>학교에서</p>', 'ko')
    assert 'data-lookup="학교"' in result
    assert '학교에서' in result

def test_wrap_korean_bare_noun_has_data_lookup():
    # Multi-char bare noun: word == lookup, but data-lookup is still set
    # because the multi-char rule always emits data-lookup for len > 1
    result = wrap_with_segmenter('<p>학교</p>', 'ko')
    assert 'data-lookup="학교"' in result
    assert '학교' in result

def test_wrap_preserves_non_cjk_as_plain_text():
    result = wrap_with_segmenter('<p>Hello, 世界!</p>', 'zh')
    assert 'Hello,' in result
    assert '<span class="w"' in result

def test_wrap_does_not_break_ruby():
    result = wrap_with_segmenter('<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>', 'ja')
    assert '<ruby>' in result
    assert '<rt>' in result
    assert '<span class="w">漢</span>' not in result

def test_wrap_preserves_existing_tags():
    result = wrap_with_segmenter('<p><strong>中文</strong></p>', 'zh')
    assert '<strong>' in result
    assert '<span class="w"' in result


# ── Phase 6: server-side EPUB metadata cache ─────────────────────────────────

import server as _server_module

def test_cached_epub_meta_caches_on_second_call(tmp_path, monkeypatch):
    """parse_epub_metadata is called only once for the same mtime."""
    epub = tmp_path / 'test.epub'
    epub.write_bytes(make_epub())
    # Clear the cache so we start fresh
    _server_module._meta_cache.clear()

    parse_calls = []
    original_parse = _server_module.parse_epub_metadata
    def counted_parse(data):
        parse_calls.append(1)
        return original_parse(data)
    monkeypatch.setattr(_server_module, 'parse_epub_metadata', counted_parse)

    _server_module._cached_epub_meta(epub)
    _server_module._cached_epub_meta(epub)
    assert len(parse_calls) == 1   # second call is a cache hit

def test_cached_epub_meta_invalidates_on_mtime_change(tmp_path, monkeypatch):
    """Cache is invalidated when file mtime changes."""
    epub = tmp_path / 'test.epub'
    epub.write_bytes(make_epub(title='Old Title'))
    _server_module._meta_cache.clear()

    _server_module._cached_epub_meta(epub)

    # Write new bytes — this changes mtime
    epub.write_bytes(make_epub(title='New Title'))
    meta = _server_module._cached_epub_meta(epub)
    assert meta['title'] == 'New Title'

def test_cached_epub_meta_returns_none_for_invalid_epub(tmp_path):
    """Returns None for corrupt/unreadable EPUBs instead of raising."""
    epub = tmp_path / 'bad.epub'
    epub.write_bytes(b'not an epub')
    _server_module._meta_cache.clear()
    result = _server_module._cached_epub_meta(epub)
    assert result is None

def test_library_uses_cache(client, tmp_path, monkeypatch):
    """Two /library requests hit parse_epub_metadata only once per book."""
    _server_module._meta_cache.clear()
    epub_data = make_epub(title='Test', language='zh', chapters=2)
    (tmp_path / 'my-book.epub').write_bytes(epub_data)

    parse_calls = []
    original_parse = _server_module.parse_epub_metadata
    def counted_parse(data):
        parse_calls.append(1)
        return original_parse(data)
    monkeypatch.setattr(_server_module, 'parse_epub_metadata', counted_parse)
    monkeypatch.setattr(_server_module, 'BOOKS_DIR', str(tmp_path))

    client.get('/library')
    client.get('/library')
    # parse_epub_metadata should be called at most once across both requests
    assert len(parse_calls) <= 1
