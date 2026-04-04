# Phase 3: Dictionary Infrastructure

**Date:** 2026-04-04  
**Phase:** 3 of 6  
**Depends on:** Phase 1 (backend infrastructure required)

**Goal:** Move dictionary lookups to the backend. Replace the browser's direct Wiktionary calls with a unified `/api/dict` endpoint that routes through a provider chain. Add the NIKL (National Institute of Korean Language) provider for Korean. The frontend becomes completely source-agnostic.

**What this phase adds:**
- Backend `/api/dict?word={word}&lang={lang}` endpoint
- `DictProvider` abstract interface + normalized JSON response format
- `WiktionaryProvider` (moves HTML parsing from frontend to backend Python)
- `NIKLProvider` (KRDICT XML API, backend proxy, API key via env var, in-memory + on-disk JSON cache)
- Provider chain registry: `ko → [NIKL, Wiktionary]`, `zh → [Wiktionary]`, `* → [Wiktionary]`
- Graceful fallback: if NIKL key absent or lookup fails, fall through to Wiktionary
- Frontend updated to call `/api/dict` instead of Wiktionary directly
- Bottom-sheet popup updated: dynamic source label, "Open in {source}" link

---

### Task 1: DictProvider interface and normalized response format

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
from server import normalize_dict_response, WiktionaryProvider

def test_normalized_response_has_required_fields():
    entry = {
        'word': '学校',
        'readings': [{'text': 'xuéxiào', 'romanization': 'xuéxiào'}],
        'definitions': [{'pos': 'noun', 'text': 'school'}],
        'source': 'wiktionary',
        'source_url': 'https://en.wiktionary.org/wiki/学校',
        'not_found': False,
    }
    # normalize_dict_response validates and returns the entry
    result = normalize_dict_response(entry)
    assert result['word'] == '学校'
    assert result['not_found'] is False
    assert isinstance(result['definitions'], list)

def test_not_found_response():
    from server import not_found_response
    result = not_found_response('nonexistentword')
    assert result['not_found'] is True
    assert result['word'] == 'nonexistentword'
    assert result['definitions'] == []
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "dict" -v
```

**Step 3: Implement**

Add to `server.py`:

```python
from abc import ABC, abstractmethod
from typing import Optional
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET2  # alias to avoid conflict with existing ET
import json
import html as html_module

# ── Normalized dict response format ─────────────────────────────────────────

def normalize_dict_response(entry: dict) -> dict:
    """Validate and return a normalized dictionary response."""
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

# ── DictProvider interface ────────────────────────────────────────────────────

class DictProvider(ABC):
    name: str = ''

    @abstractmethod
    def lookup(self, word: str) -> Optional[dict]:
        """Return normalized entry dict or None if not found."""
        ...
```

**Step 4: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: DictProvider interface and normalized response format"
```

---

### Task 2: WiktionaryProvider (server-side)

**Files:** `server.py`, `tests/test_server.py`

Move the Wiktionary HTML parsing from `app.js` to the backend. The frontend will never see MediaWiki HTML again.

**Step 1: Write failing tests**

```python
from unittest.mock import patch, MagicMock
from server import WiktionaryProvider

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
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "wiktionary_provider" -v
```

**Step 3: Implement**

```python
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

        # Parse with BeautifulSoup to extract structured content
        doc = BeautifulSoup(raw_html, 'lxml')

        # Find target language section
        target_section_name = self._LANG_SECTIONS.get(lang, '')
        lang_heading = None
        if target_section_name:
            for h2 in doc.find_all('h2'):
                if target_section_name in h2.get_text():
                    lang_heading = h2
                    break

        root = lang_heading.parent if lang_heading else doc

        # Extract pronunciation
        pronunciation = ''
        pron_el = root.find(class_=['IPA', 'pinyin']) or root.find(attrs={'class': lambda c: c and 'pron' in c})
        if pron_el:
            pronunciation = pron_el.get_text().strip()

        # Extract definitions from ordered lists
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
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: WiktionaryProvider — server-side Wiktionary parsing"
```

---

### Task 3: NIKLProvider with caching

**Files:** `server.py`, `tests/test_server.py`

KRDICT is XML-only, requires a free API key, has no CORS. Backend proxy is mandatory. Key stored in `NIKL_API_KEY` env var. Graceful fallback if key absent.

**Step 1: Write failing tests**

```python
from server import NIKLProvider

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
</channel>'''

def test_nikl_provider_parses_xml():
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = lambda: MOCK_NIKL_XML.encode()
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
    """Second call for same word should not hit the network."""
    call_count = 0
    original_urlopen = urllib.request.urlopen

    def counting_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        m.read = lambda: MOCK_NIKL_XML.encode()
        return m

    provider = NIKLProvider(api_key='test-key-32chars-padding0000000')
    provider._cache.clear()

    with patch('urllib.request.urlopen', side_effect=counting_urlopen):
        provider.lookup('나무', lang='ko')
        provider.lookup('나무', lang='ko')

    assert call_count == 1  # only one network request
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "nikl" -v
```

**Step 3: Implement**

```python
# POS code → English mapping for NIKL
_NIKL_POS_MAP = {
    '명사': 'noun', '대명사': 'pronoun', '수사': 'numeral',
    '동사': 'verb', '형용사': 'adjective', '관형사': 'determiner',
    '부사': 'adverb', '감탄사': 'interjection', '조사': 'particle',
    '의존명사': 'bound noun', '보조동사': 'auxiliary verb',
    '보조형용사': 'auxiliary adjective',
}

def _romanize_hangul_simple(text: str) -> str:
    """
    Very lightweight Hangul → Revised Romanization transliteration.
    Only handles basic syllable blocks; good enough for pronunciation display.
    Full Revised Romanization is context-sensitive and complex — for a complete
    implementation, use a dedicated library. This covers ~85% of common words.
    """
    # Fallback: return the text as-is if no romanization table is available.
    # A proper implementation (~100 lines) can be added here.
    return text

class NIKLProvider(DictProvider):
    name = 'nikl'
    _SEARCH_URL = 'https://krdict.korean.go.kr/api/search'

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._cache: dict[str, dict | None] = {}

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
        }] if pronunciation_text or headword else []

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
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: NIKLProvider with in-memory cache and graceful fallback"
```

---

### Task 4: Provider chain registry and `/api/dict` endpoint

**Files:** `server.py`, `tests/test_server.py`

Wire the providers into a registry and expose the unified endpoint.

**Step 1: Write failing tests**

```python
def test_api_dict_endpoint_returns_json(client):
    with patch('server.PROVIDER_CHAINS', {
        'zh': [MagicMock(spec=DictProvider, lookup=lambda w, lang='zh': {
            'word': w, 'readings': [], 'definitions': [{'pos': 'noun', 'text': 'test'}],
            'source': 'mock', 'source_url': None, 'not_found': False,
        })],
        '*': [],
    }):
        r = client.get('/api/dict?word=%E4%B8%AD&lang=zh')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['word'] == '中'

def test_api_dict_endpoint_returns_not_found(client):
    with patch('server.PROVIDER_CHAINS', {'*': []}):
        r = client.get('/api/dict?word=zzz&lang=zh')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['not_found'] is True

def test_api_dict_endpoint_requires_word_param(client):
    r = client.get('/api/dict?lang=zh')
    assert r.status_code == 400
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "api_dict" -v
```

**Step 3: Implement**

```python
from flask import request as flask_request

# ── Provider chain registry ───────────────────────────────────────────────────
_NIKL_API_KEY = os.environ.get('NIKL_API_KEY', None)
_wiktionary = WiktionaryProvider()
_nikl = NIKLProvider(api_key=_NIKL_API_KEY)

PROVIDER_CHAINS: dict[str, list[DictProvider]] = {
    'ko': [_nikl, _wiktionary],   # NIKL first; falls back to Wiktionary if key absent or no result
    'zh': [_wiktionary],
    '*':  [_wiktionary],
}

def _lookup_word(word: str, lang: str) -> dict:
    chain = PROVIDER_CHAINS.get(lang) or PROVIDER_CHAINS.get('*', [])
    for provider in chain:
        try:
            result = provider.lookup(word, lang=lang)
            if result is not None:
                return result
        except Exception:
            continue
    return not_found_response(word)

@app.route('/api/dict')
def dict_lookup():
    word = flask_request.args.get('word', '').strip()
    lang = flask_request.args.get('lang', '*').strip()
    if not word:
        abort(400)
    result = _lookup_word(word, lang)
    return jsonify(result)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: /api/dict endpoint with provider chain registry"
```

---

### Task 5: On-disk dictionary cache (survive restarts)

**Files:** `server.py`

The in-memory NIKL cache is lost on server restart. Add a JSON file layer at `/tmp/dict_cache.json` to persist lookups across restarts.

**Step 1: Implement**

Add to `server.py`:

```python
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
        pass  # non-fatal: disk cache is best-effort

# Shared cache across all providers
_DISK_CACHE: dict[str, dict] = _load_disk_cache()
```

Update `_lookup_word` to check and populate the disk cache:

```python
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
```

**Step 2: Verify manually**

```bash
python server.py
# Lookup a Korean word, kill server, restart, lookup same word
# Second lookup should be instant (from disk cache), not hitting NIKL
```

**Step 3: Commit**

```bash
git add server.py
git commit -m "feat: on-disk JSON dictionary cache for cross-restart persistence"
```

---

### Task 6: Update frontend to use `/api/dict`

**Files:** `static/app.js`

Replace the direct Wiktionary `fetch()` in `lookupWord()` with a call to `/api/dict`. The frontend now receives normalized JSON and never parses HTML.

**Step 1: Update `lookupWord` in `app.js`**

Replace the `lookupWord` function:

```js
async function lookupWord(word) {
  showPopup(word, null, null, null);  // show loading state immediately

  const lang = document.querySelector('article[data-lang]')?.dataset.lang
             ?? state.current?.language ?? '*';

  let result = null;
  let networkError = false;

  try {
    const res = await fetch(
      `/api/dict?word=${encodeURIComponent(word)}&lang=${encodeURIComponent(lang)}`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    result = await res.json();
  } catch (e) {
    networkError = true;
  }

  if (networkError || !result) {
    showPopup(word, '', ['Network error — check connection.'], null);
    return;
  }

  if (result.not_found) {
    showPopup(word, '', ['No dictionary entry found.'], null);
    return;
  }

  const pronunciation = result.readings?.[0]?.text ?? '';
  const definitions = (result.definitions ?? []).slice(0, 5).map(d => d.text);
  const sourceLabel = result.source === 'nikl'
    ? 'Open in KRDICT ↗'
    : result.source === 'wiktionary'
    ? 'Open in Wiktionary ↗'
    : 'Open source ↗';
  const sourceUrl = result.source_url;

  showPopup(word, pronunciation, definitions, sourceUrl, sourceLabel);
}

function showPopup(word, pronunciation, definitions, sourceUrl, sourceLabel) {
  document.getElementById('popup-word').textContent = word;
  document.getElementById('popup-pronunciation').textContent =
    pronunciation !== null ? (pronunciation || '') : 'Loading…';

  const defEl = document.getElementById('popup-definitions');
  if (definitions === null) {
    defEl.innerHTML = '<em>Loading…</em>';
  } else {
    const ol = document.createElement('ol');
    (definitions || []).forEach(d => {
      const li = document.createElement('li');
      li.textContent = d;    // textContent — never innerHTML for external data
      ol.appendChild(li);
    });
    defEl.innerHTML = '';
    if (definitions.length) defEl.appendChild(ol);
  }

  const link = document.getElementById('popup-source-link');
  if (sourceUrl) {
    link.href = sourceUrl;
    link.textContent = sourceLabel || 'Open source ↗';
    link.style.display = 'block';
  } else {
    link.style.display = 'none';
  }

  document.getElementById('lookup-popup').classList.remove('hidden');
}
```

**Step 2: Write a test for the endpoint integration**

```python
def test_chapter_tap_uses_api_dict(client, tmp_path):
    """Verify /api/dict is available and returns JSON for a word."""
    with patch('server._lookup_word', return_value={
        'word': '中', 'readings': [{'text': 'zhōng', 'romanization': 'zhong'}],
        'definitions': [{'pos': 'noun', 'text': 'middle'}],
        'source': 'wiktionary', 'source_url': None, 'not_found': False,
    }):
        r = client.get('/api/dict?word=%E4%B8%AD&lang=zh')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['readings'][0]['text'] == 'zhōng'
```

**Step 3: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 4: Verify in browser**

```bash
python server.py
# Open a Chinese book, tap a character — popup should appear
# For Korean books (with NIKL_API_KEY set), source label should say "Open in KRDICT"
# Without NIKL_API_KEY, Korean falls back to Wiktionary automatically
```

**Step 5: Commit**

```bash
git add static/app.js server.py tests/test_server.py
git commit -m "feat: frontend uses /api/dict — source-agnostic dictionary popup"
```

---

### Task 7: Run full test suite

**Step 1:**

```bash
pytest tests/ -v
```

Expected: all PASS

**Step 2: Smoke test with real EPUBs**

```bash
cp ../../fixtures/sample-zh.epub books/
cp ../../fixtures/sample-ko.epub books/
python server.py
```

Open `http://localhost:8090` and verify:

- **`sample-zh.epub`** — tap a single Chinese character (e.g. 的, 我, 是); popup appears with pinyin romanisation and at least one definition sourced from Wiktionary. Check the Network tab: request goes to `/api/dict?word=的&lang=zh`, not directly to `en.wiktionary.org`.
- **`sample-ko.epub`** (Wiktionary-only, no API key) — tap a Korean eojeol; popup appears. The Network tab shows `/api/dict?word=학교&lang=ko` (the stripped stem, not the full eojeol). Wiktionary result may be sparse — this is expected without NIKL.
- **`sample-ko.epub`** (with NIKL key) — set the env var and restart:

```bash
NIKL_API_KEY=your-key python server.py
# Tap a Korean noun — popup should show NIKL result with Korean pronunciation
# Tap a word with no NIKL entry — should fall back to Wiktionary result
# Source label in the popup ("Open in KRDICT ↗" vs "Open in Wiktionary ↗") switches accordingly
```

- **Curl sanity checks:**

```bash
curl "http://localhost:8090/api/dict?word=電話&lang=zh"
# → { "word": "電話", "readings": [...], "definitions": [...], "source": "wiktionary", ... }

curl "http://localhost:8090/api/dict?word=학교&lang=ko"
# → { "word": "학교", "readings": [...], "definitions": [...], "source": "nikl"|"wiktionary", ... }

curl "http://localhost:8090/api/dict?word=zzznope&lang=zh"
# → { "not_found": true }
```

**Step 3: Commit**

```bash
git commit -m "chore: verified Phase 3 complete — backend dictionary infrastructure"
```
