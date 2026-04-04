import zipfile, io, os, re, base64, sqlite3, datetime, mimetypes, posixpath, json
import xml.etree.ElementTree as ET
import xml.etree.ElementTree as ET2  # alias for NIKL XML parsing
import urllib.request
import urllib.parse
import jieba
from abc import ABC, abstractmethod
from typing import Optional
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


@app.route('/book/<book_id>/asset/<path:asset_path>')
def asset(book_id, asset_path):
    _validate_book_id(book_id)
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


# ── HTML normalization pipeline ───────────────────────────────────────────────

_STYLE_ALLOWLIST = {'font-style', 'font-weight', 'vertical-align'}
_VERTICAL_ALIGN_VALUES = {'sub', 'super'}


def _parse_inline_style(style_str: str) -> dict:
    result = {}
    for decl in style_str.split(';'):
        decl = decl.strip()
        if ':' in decl:
            prop, _, val = decl.partition(':')
            result[prop.strip().lower()] = val.strip()
    return result


def _filter_inline_style(style_str: str) -> str | None:
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
    if src.startswith('data:') or src.startswith('http'):
        return src
    resolved = posixpath.normpath(posixpath.join(chapter_base, src))
    resolved = resolved.lstrip('/')
    return f'/book/{book_id}/asset/{resolved}'


def normalize_html(html: str, book_id: str, lang: str, chapter_base: str = 'OEBPS') -> str:
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup.find_all(['script', 'style', 'iframe', 'object', 'embed']):
        tag.decompose()
    for tag in soup.find_all('font'):
        tag.unwrap()
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
                if str(tag.get('href', '')).strip().lower().startswith('javascript:'):
                    attrs_to_remove.append(attr)
            elif attr == 'src':
                if str(tag.get('src', '')).strip().lower().startswith('javascript:'):
                    attrs_to_remove.append(attr)
            elif attr == 'style':
                filtered = _filter_inline_style(str(tag.get('style', '')))
                if filtered:
                    tag['style'] = filtered
                else:
                    attrs_to_remove.append(attr)
        for attr in attrs_to_remove:
            del tag[attr]
        if tag.name == 'img':
            tag.attrs.pop('width', None)
            tag.attrs.pop('height', None)
            src = tag.get('src', '')
            if src and not src.startswith('/book/') and not src.startswith('http'):
                tag['src'] = _rewrite_epub_src(src, book_id, chapter_base)
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
            br.replace_with(BeautifulSoup('<p></p>', 'lxml').find('p'))
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


# ── CJK wrapping ──────────────────────────────────────────────────────────────

_CJK_RE = re.compile(
    r'[\u4e00-\u9fff'
    r'\u3400-\u4dbf'
    r'\U00020000-\U0002a6df'
    r'\uac00-\ud7a3]'
)

# ── Segmenter interface + implementations ─────────────────────────────────────

jieba.initialize()  # pre-load trie at startup


class Segmenter(ABC):
    @abstractmethod
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        """
        Returns list of (word, lookup_term) pairs.
        - word: the display string
        - lookup_term: the dictionary key. If == word, no data-lookup attr needed.
                       If None, the token is non-word content and should be emitted
                       as plain text without a <span>.
        """
        ...


class JiebaSegmenter(Segmenter):
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        result = []
        for token in jieba.cut(text):
            if not token:
                continue
            if _CJK_RE.search(token):
                result.append((token, token))
            else:
                result.append((token, None))
        return result


_JOSA_LIST = sorted([
    '에서', '에게', '으로', '부터', '까지', '처럼', '보다',
    '한테', '이라', '이다', '로', '에', '의', '도', '만',
    '와', '과', '이', '가', '을', '를', '은', '는',
    '들이', '들은', '들을', '들의', '들도', '들만',
], key=len, reverse=True)


class KoreanJosaSegmenter(Segmenter):
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        tokens = text.split(' ')
        result = []
        for i, token in enumerate(tokens):
            if i > 0:
                result.append((' ', None))
            if not token:
                continue
            if not _CJK_RE.search(token):
                result.append((token, token))
                continue
            stem = token
            for josa in _JOSA_LIST:
                if token.endswith(josa) and len(token) > len(josa):
                    stem = token[:-len(josa)]
                    break
            result.append((token, stem))
        return result


class WhitespaceSegmenter(Segmenter):
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        tokens = text.split(' ')
        result = []
        for i, token in enumerate(tokens):
            if i > 0:
                result.append((' ', None))
            if token:
                result.append((token, token))
        return result


SEGMENTERS: dict[str, Segmenter] = {
    'zh':    JiebaSegmenter(),
    'zh-TW': JiebaSegmenter(),
    'zh-HK': JiebaSegmenter(),
    'ko':    KoreanJosaSegmenter(),
}


def get_segmenter(lang: str) -> Segmenter:
    return SEGMENTERS.get(lang, WhitespaceSegmenter())


def _tokens_to_html(tokens: list[tuple[str, str | None]]) -> str:
    parts = []
    for word, lookup in tokens:
        if lookup is None:
            parts.append(word)
        elif len(word) == 1:
            # Single character — no data-lookup needed; innerText is the lookup
            parts.append(f'<span class="w">{word}</span>')
        else:
            # Multi-char token — always set data-lookup (even if word == lookup)
            # so the frontend doesn't have to rely on innerText for compounds
            parts.append(f'<span class="w" data-lookup="{lookup}">{word}</span>')
    return ''.join(parts)


def wrap_with_segmenter(html: str, language: str) -> str:
    segmenter = get_segmenter(language)
    soup = BeautifulSoup(html, 'lxml')

    # First pass: wrap <ruby> elements as single units
    for ruby in soup.find_all('ruby'):
        base_text = ''.join(
            node for node in ruby.strings
            if node.parent.name not in ('rt', 'rp', 'rtc')
        ).strip()
        wrapper = soup.new_tag('span', **{'class': 'w'})
        if base_text:
            wrapper['data-lookup'] = base_text
        ruby.wrap(wrapper)

    # Second pass: process leaf text nodes outside ruby
    for node in soup.find_all(string=True):
        parent = node.parent
        if not parent:
            continue
        if parent.name in ('script', 'style', 'span'):
            continue
        if parent.name == 'ruby' or any(p.name == 'ruby' for p in parent.parents):
            continue

        text = str(node)
        if not text.strip():
            continue

        tokens = segmenter.segment(text)
        new_html = _tokens_to_html(tokens)
        if new_html != text:
            new_soup = BeautifulSoup(new_html, 'lxml')
            new_nodes = list(new_soup.body.children)
            node.replace_with(*[n.extract() for n in new_nodes])

    body = soup.find('body')
    return body.decode_contents() if body else str(soup)


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


def extract_chapter(data: bytes, index: int, book_id: str = '') -> str | None:
    paths = get_chapter_paths(data)
    if index >= len(paths):
        return None
    chapter_path = paths[index]
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


# ── Chapter + static ──────────────────────────────────────────────────────────

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
    wrapped = wrap_with_segmenter(content, lang)
    fragment = f'<article data-lang="{lang}">{wrapped}</article>'
    return Response(fragment, mimetype='text/html; charset=utf-8')


# ── Dictionary infrastructure ─────────────────────────────────────────────────

def normalize_dict_response(entry: dict) -> dict:
    return {
        'word': entry.get('word', ''),
        'readings': entry.get('readings', []),
        'definitions': entry.get('definitions', []),
        'source': entry.get('source', ''),
        'source_url': entry.get('source_url', None),
        'not_found': bool(entry.get('not_found', False)),
    }


def not_found_response(word: str) -> dict:
    return normalize_dict_response({
        'word': word,
        'readings': [],
        'definitions': [],
        'source': '',
        'source_url': None,
        'not_found': True,
    })


class DictProvider(ABC):
    name: str = ''

    @abstractmethod
    def lookup(self, word: str, lang: str = '*') -> Optional[dict]:
        """Return normalized entry dict or None if not found."""
        ...


class WiktionaryProvider(DictProvider):
    name = 'wiktionary'
    _LANG_SECTIONS = {'zh': 'Chinese', 'ko': 'Korean', 'ja': 'Japanese'}

    def lookup(self, word: str, lang: str = '*') -> Optional[dict]:
        url = (
            'https://en.wiktionary.org/w/api.php?action=parse'
            f'&page={urllib.parse.quote(word)}&prop=text&format=json'
        )
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
        except Exception:
            return None

        if 'error' in data or 'parse' not in data:
            return None

        raw_html = data['parse']['text']['*']
        doc = BeautifulSoup(raw_html, 'lxml')

        target_section_name = self._LANG_SECTIONS.get(lang, '')
        lang_heading = None
        if target_section_name:
            for h2 in doc.find_all('h2'):
                if target_section_name in h2.get_text():
                    lang_heading = h2
                    break

        root = lang_heading.parent if lang_heading else doc

        pronunciation = ''
        pron_el = root.find(class_=['IPA', 'pinyin'])
        if pron_el:
            pronunciation = pron_el.get_text().strip()

        definitions = []
        for li in root.select('ol li'):
            text = li.get_text(' ', strip=True)
            if text and len(text) > 1 and not text.startswith('['):
                definitions.append({'pos': None, 'text': text})
            if len(definitions) >= 5:
                break

        if not definitions:
            return None

        readings = []
        if pronunciation:
            readings.append({'text': pronunciation, 'romanization': None})

        source_url = f'https://en.wiktionary.org/wiki/{urllib.parse.quote(word)}'
        return normalize_dict_response({
            'word': word,
            'readings': readings,
            'definitions': definitions,
            'source': 'wiktionary',
            'source_url': source_url,
            'not_found': False,
        })


_NIKL_POS_MAP = {
    '명사': 'noun', '대명사': 'pronoun', '수사': 'numeral',
    '동사': 'verb', '형용사': 'adjective', '관형사': 'determiner',
    '부사': 'adverb', '감탄사': 'interjection', '조사': 'particle',
    '의존명사': 'bound noun', '보조동사': 'auxiliary verb',
    '보조형용사': 'auxiliary adjective',
}


def _romanize_hangul_simple(text: str) -> str:
    return text  # stub; returns text as-is


class NIKLProvider(DictProvider):
    name = 'nikl'
    _SEARCH_URL = 'https://krdict.korean.go.kr/api/search'

    def __init__(self, api_key=None):
        self._api_key = api_key
        self._cache: dict = {}

    def lookup(self, word: str, lang: str = 'ko') -> Optional[dict]:
        if not self._api_key:
            return None

        cache_key = word.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        params = urllib.parse.urlencode({
            'key': self._api_key,
            'q': word,
            'num': '5',
            'translated': 'y',
            'trans_lang': '1',
        })
        url = f'{self._SEARCH_URL}?{params}'

        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                xml_bytes = resp.read()
        except Exception:
            self._cache[cache_key] = None
            return None

        try:
            root_el = ET2.fromstring(xml_bytes)
        except ET2.ParseError:
            self._cache[cache_key] = None
            return None

        items = root_el.findall('.//item')
        if not items:
            self._cache[cache_key] = None
            return None

        item = items[0]
        headword = item.findtext('word', word)
        pronunciation_text = item.findtext('pronunciation', '')
        pos_ko = item.findtext('pos', '')
        pos_en = _NIKL_POS_MAP.get(pos_ko, pos_ko.lower() if pos_ko else None)
        source_url = item.findtext('link', '')

        definitions = []
        for sense in item.findall('sense'):
            trans = sense.find('translation')
            if trans is not None:
                trans_word = trans.findtext('trans_word', '')
                trans_dfn = trans.findtext('trans_dfn', '')
                text_en = f'{trans_word}: {trans_dfn}'.strip(': ') if trans_word or trans_dfn else ''
            else:
                text_en = ''
            text_ko = sense.findtext('definition', '')
            if text_en or text_ko:
                definitions.append({
                    'pos': pos_en,
                    'text': text_en or text_ko,
                    'text_ko': text_ko if text_en else None,
                })

        readings = [{
            'text': pronunciation_text or headword,
            'romanization': _romanize_hangul_simple(pronunciation_text or headword),
        }] if (pronunciation_text or headword) else []

        result = normalize_dict_response({
            'word': headword,
            'readings': readings,
            'definitions': definitions,
            'source': 'nikl',
            'source_url': source_url or None,
            'not_found': False,
        })
        self._cache[cache_key] = result
        return result


_NIKL_API_KEY = os.environ.get('NIKL_API_KEY', None)
_wiktionary = WiktionaryProvider()
_nikl = NIKLProvider(api_key=_NIKL_API_KEY)

PROVIDER_CHAINS: dict = {
    'ko': [_nikl, _wiktionary],
    'zh': [_wiktionary],
    '*':  [_wiktionary],
}

_DICT_CACHE_PATH = os.environ.get('DICT_CACHE_PATH', '/tmp/dict_cache.json')


def _load_disk_cache() -> dict:
    try:
        with open(_DICT_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_disk_cache(cache: dict) -> None:
    try:
        with open(_DICT_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass


_DISK_CACHE: dict = _load_disk_cache()


def _lookup_word(word: str, lang: str) -> dict:
    cache_key = f'{lang}:{word}'
    if cache_key in _DISK_CACHE:
        return _DISK_CACHE[cache_key]

    chain = PROVIDER_CHAINS.get(lang) or PROVIDER_CHAINS.get('*', [])
    for provider in chain:
        try:
            result = provider.lookup(word, lang=lang)
            if result is not None:
                _DISK_CACHE[cache_key] = result
                _save_disk_cache(_DISK_CACHE)
                return result
        except Exception:
            continue
    return not_found_response(word)


@app.route('/api/dict')
def dict_lookup():
    word = request.args.get('word', '').strip()
    lang = request.args.get('lang', '*').strip()
    if not word:
        abort(400)
    result = _lookup_word(word, lang)
    return jsonify(result)


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
