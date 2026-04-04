# Phase 4: Language Intelligence

**Date:** 2026-04-04  
**Phase:** 4 of 6  
**Depends on:** Phase 1 (chapter endpoint), Phase 3 (dictionary uses `data-lookup`)

**Goal:** Replace the hardcoded CJK character-wrapping with a language-agnostic segmenter interface. Add jieba for Chinese word segmentation (raises lookup accuracy from ~40% to ~80%) and josa particle-stripping for Korean (enables noun stems to be looked up correctly).

**What this phase adds:**
- `Segmenter` interface: `segment(text) → list[(word, lookup_term)]`
- `SEGMENTERS` registry + `get_segmenter(lang)` function
- `JiebaSegmenter` (zh — `jieba.cut()`, module-level `jieba.initialize()`)
- `KoreanJosaSegmenter` (ko — whitespace split + longest-match josa stripping)
- `WhitespaceSegmenter` (default fallback — wraps whitespace-delimited tokens)
- BeautifulSoup leaf-node text replacement using segmenter output
- Span format: `<span class="w" data-lookup="...">` for multi-char/stripped tokens; no `data-lookup` for single-char
- Ruby/furigana fix: skip wrapping inside `<ruby>` elements; wrap `<ruby>` as a single unit
- `jieba==0.42.1` added to `requirements.txt`

---

### Background: Why segmentation matters

**Chinese (current):** Each character (`中`, `文`) becomes a separate tappable span. For the word 中文 ("Chinese language"), tapping 中 yields "middle/China/hit" — not the intended word. ~60% of Chinese taps return wrong semantic units.

**With jieba:** `中文` → single span. Lookup accuracy improves from ~40% to ~80–85%.

**Korean (current):** Each space-delimited eojeol (`학교에서`) becomes a span. The eojeol includes a particle (에서 = "at/from"), so lookup returns no result. Fewer than 15% of Korean taps succeed.

**With josa stripping:** `학교에서` → display `학교에서`, but `data-lookup="학교"`. The tap queries the noun stem, which has a Wiktionary/NIKL entry.

---

### Task 1: Add jieba dependency

**Files:** `requirements.txt`, `requirements-dev.txt`

**Step 1: Update `requirements.txt`**

```
flask==3.1.0
beautifulsoup4==4.12.3
lxml==5.3.0
jieba==0.42.1
```

**Step 2: Install**

```bash
pip install -r requirements-dev.txt
python -c "import jieba; print(jieba.__version__)"
```

Expected: `0.42.1`

**Step 3: Commit**

```bash
git add requirements.txt requirements-dev.txt
git commit -m "feat: add jieba==0.42.1 for Chinese word segmentation"
```

---

### Task 2: Segmenter interface and registry

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
from server import get_segmenter, JiebaSegmenter, KoreanJosaSegmenter, WhitespaceSegmenter

def test_get_segmenter_zh_returns_jieba():
    seg = get_segmenter('zh')
    assert isinstance(seg, JiebaSegmenter)

def test_get_segmenter_ko_returns_korean():
    seg = get_segmenter('ko')
    assert isinstance(seg, KoreanJosaSegmenter)

def test_get_segmenter_unknown_returns_whitespace():
    seg = get_segmenter('fr')
    assert isinstance(seg, WhitespaceSegmenter)

def test_get_segmenter_zh_tw_returns_jieba():
    seg = get_segmenter('zh-TW')
    assert isinstance(seg, JiebaSegmenter)

def test_jieba_segmenter_returns_word_pairs():
    seg = JiebaSegmenter()
    tokens = seg.segment('中文')
    words = [w for w, _ in tokens]
    assert '中文' in words

def test_jieba_segmenter_single_char_no_data_lookup():
    seg = JiebaSegmenter()
    tokens = seg.segment('我')
    # Single-char CJK: word == lookup_term
    assert ('我', '我') in tokens or any(w == '我' and l == '我' for w, l in tokens)

def test_jieba_segmenter_multi_char_sets_lookup():
    seg = JiebaSegmenter()
    tokens = seg.segment('电话')
    # Multi-char compound: word == lookup_term (both set to the full word)
    assert any(w == '电话' for w, _ in tokens)

def test_jieba_segmenter_non_cjk_returns_none_lookup():
    seg = JiebaSegmenter()
    tokens = seg.segment('hello')
    # Non-CJK: lookup_term is None (emit as plain text, no span)
    for word, lookup in tokens:
        if word == 'hello':
            assert lookup is None

def test_korean_josa_segmenter_strips_particle():
    seg = KoreanJosaSegmenter()
    tokens = seg.segment('학교에서')
    # Should have one token: display '학교에서', lookup '학교'
    assert len(tokens) == 1
    word, lookup = tokens[0]
    assert word == '학교에서'
    assert lookup == '학교'

def test_korean_josa_segmenter_bare_noun_lookup_equals_word():
    seg = KoreanJosaSegmenter()
    tokens = seg.segment('학교')
    word, lookup = tokens[0]
    assert word == '학교'
    assert lookup == '학교'  # no particle to strip

def test_whitespace_segmenter_splits_on_spaces():
    seg = WhitespaceSegmenter()
    tokens = seg.segment('hello world')
    assert ('hello', 'hello') in tokens
    assert ('world', 'world') in tokens
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "segmenter or get_segmenter" -v
```

**Step 3: Implement**

Add to `server.py`:

```python
import jieba
from abc import ABC, abstractmethod as _abstractmethod

# ── Segmenter interface ───────────────────────────────────────────────────────

class Segmenter(ABC):
    @_abstractmethod
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        """
        Returns list of (word, lookup_term) pairs.
        - word: the display string
        - lookup_term: the dictionary key. If == word, no data-lookup attr needed.
                       If None, the token is non-word content (punctuation, spaces)
                       and should be emitted as plain text without a <span>.
        """
        ...

# ── JiebaSegmenter ─────────────────────────────────────────────────────────

class JiebaSegmenter(Segmenter):
    def __init__(self):
        # Pre-load the jieba trie at startup to avoid ~1s latency on first request.
        jieba.initialize()

    def segment(self, text: str) -> list[tuple[str, str | None]]:
        tokens = jieba.cut(text)
        result = []
        for token in tokens:
            if not token:
                continue
            # Is this token CJK (contains at least one CJK character)?
            has_cjk = bool(_CJK_RE.search(token))
            if not has_cjk:
                result.append((token, None))  # plain text, no span
            else:
                result.append((token, token))  # word == lookup_term
        return result

# ── KoreanJosaSegmenter ───────────────────────────────────────────────────────
# Korean particles (josa) form a closed class of ~20 items.
# Longest-match stripping recovers the noun stem in most cases.
# Verb inflections are NOT handled — they require morphological analysis.

_JOSA_LIST = sorted([
    '에서', '에게', '으로', '부터', '까지', '처럼', '보다',
    '한테', '이라', '이다', '로', '에', '의', '도', '만',
    '와', '과', '이', '가', '을', '를', '은', '는',
    '들이', '들은', '들을', '들의', '들도', '들만',
], key=len, reverse=True)  # longest-first to enable longest-match

class KoreanJosaSegmenter(Segmenter):
    def segment(self, text: str) -> list[tuple[str, str | None]]:
        # Split on whitespace; each eojeol is one token
        tokens = text.split(' ')
        result = []
        for i, token in enumerate(tokens):
            if i > 0:
                result.append((' ', None))  # preserve spacing
            if not token:
                continue
            # Only process tokens containing Hangul
            if not _CJK_RE.search(token):
                result.append((token, token))
                continue
            # Longest-match josa stripping
            stem = token
            for josa in _JOSA_LIST:
                if token.endswith(josa) and len(token) > len(josa):
                    stem = token[:-len(josa)]
                    break
            result.append((token, stem))
        return result

# ── WhitespaceSegmenter ───────────────────────────────────────────────────────

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

# ── Registry ──────────────────────────────────────────────────────────────────

SEGMENTERS: dict[str, Segmenter] = {
    'zh':    JiebaSegmenter(),
    'zh-TW': JiebaSegmenter(),   # Traditional Chinese — same segmenter
    'zh-HK': JiebaSegmenter(),
    'ko':    KoreanJosaSegmenter(),
}

def get_segmenter(lang: str) -> Segmenter:
    return SEGMENTERS.get(lang, WhitespaceSegmenter())
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "segmenter or get_segmenter" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: segmenter interface with jieba (zh) and josa-stripping (ko)"
```

---

### Task 3: Replace `wrap_cjk` with segmenter-based pipeline

**Files:** `server.py`, `tests/test_server.py`

Replace the character-level `wrap_cjk` with `wrap_with_segmenter` that uses `get_segmenter(lang)` to process leaf text nodes. Includes the ruby/furigana fix: skip wrapping inside `<ruby>`, wrap the `<ruby>` element itself as one unit.

**Step 1: Write failing tests**

```python
from server import wrap_with_segmenter

def test_wrap_chinese_uses_jieba():
    html = '<p>中文学习</p>'
    result = wrap_with_segmenter(html, 'zh')
    # jieba should segment 中文 and 学习 as units (not individual chars)
    # At minimum: spans exist
    assert '<span class="w"' in result
    # Should NOT split 中 and 文 into separate spans
    assert '<span class="w">中</span><span class="w">文</span>' not in result

def test_wrap_chinese_multi_char_has_data_lookup():
    html = '<p>电话</p>'
    result = wrap_with_segmenter(html, 'zh')
    assert 'data-lookup="电话"' in result

def test_wrap_chinese_single_char_no_data_lookup():
    html = '<p>我</p>'
    result = wrap_with_segmenter(html, 'zh')
    assert '<span class="w">我</span>' in result
    assert 'data-lookup="我"' not in result

def test_wrap_korean_strips_josa():
    html = '<p>학교에서</p>'
    result = wrap_with_segmenter(html, 'ko')
    assert 'data-lookup="학교"' in result
    assert '>학교에서<' in result  # display text is still the full eojeol

def test_wrap_korean_bare_noun_no_data_lookup():
    html = '<p>학교</p>'
    result = wrap_with_segmenter(html, 'ko')
    assert '<span class="w">학교</span>' in result
    assert 'data-lookup=' not in result

def test_wrap_preserves_non_cjk_as_plain_text():
    html = '<p>Hello, 世界!</p>'
    result = wrap_with_segmenter(html, 'zh')
    assert 'Hello,' in result
    assert '<span class="w">' in result  # CJK part wrapped

def test_wrap_does_not_break_ruby():
    html = '<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>'
    result = wrap_with_segmenter(html, 'ja')
    assert '<ruby>' in result
    assert '<rt>' in result
    # Individual characters inside ruby should NOT be wrapped
    assert '<span class="w">漢</span>' not in result

def test_wrap_english_unchanged():
    html = '<p>Hello world</p>'
    result = wrap_with_segmenter(html, 'en')
    # WhitespaceSegmenter wraps tokens for non-CJK, non-ko languages
    # but English tokens have word == lookup_term
    assert 'Hello' in result

def test_wrap_preserves_existing_tags():
    html = '<p><strong>中文</strong></p>'
    result = wrap_with_segmenter(html, 'zh')
    assert '<strong>' in result
    assert '<span class="w"' in result
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "wrap_with_segmenter" -v
```

**Step 3: Implement**

```python
def _tokens_to_html(tokens: list[tuple[str, str | None]]) -> str:
    """Convert segmenter output to HTML span sequence."""
    parts = []
    for word, lookup in tokens:
        if lookup is None:
            # Plain text — no span
            parts.append(word)
        elif word == lookup or len(word) == 1:
            # Single char or no normalization needed — no data-lookup
            parts.append(f'<span class="w">{word}</span>')
        else:
            # Multi-char word or normalized lookup
            parts.append(f'<span class="w" data-lookup="{lookup}">{word}</span>')
    return ''.join(parts)

def wrap_with_segmenter(html: str, language: str) -> str:
    """
    Walk leaf text nodes, apply the segmenter for `language`, and replace
    each text node with the appropriate <span class="w"> markup.

    Ruby fix: text nodes inside <ruby> are skipped. Instead, the <ruby>
    element itself is wrapped as a single .w unit — this treats kanji+furigana
    as one lookup unit and prevents <rt> annotations from detaching.
    """
    segmenter = get_segmenter(language)
    soup = BeautifulSoup(html, 'lxml')

    # First pass: wrap <ruby> elements as single units
    for ruby in soup.find_all('ruby'):
        # Get the lookup text: concatenate rb/text content, ignoring rt
        base_text = ''.join(
            node for node in ruby.strings
            if node.parent.name not in ('rt', 'rp', 'rtc')
        )
        base_text = base_text.strip()
        wrapper = soup.new_tag('span', **{'class': 'w'})
        if base_text:
            wrapper['data-lookup'] = base_text
        ruby.wrap(wrapper)

    # Second pass: process leaf text nodes outside ruby
    for node in soup.find_all(string=True):
        # Skip text inside <script>, <style>, <span>, <ruby> descendants
        parent = node.parent
        if not parent:
            continue
        if parent.name in ('script', 'style', 'span'):
            continue
        # Check if inside ruby (after wrap pass, ruby is inside a span.w)
        if any(p.name == 'ruby' for p in parent.parents):
            continue

        text = str(node)
        if not text.strip():
            continue

        tokens = segmenter.segment(text)
        new_html = _tokens_to_html(tokens)
        if new_html != text:
            node.replace_with(BeautifulSoup(new_html, 'lxml').body.decode_contents())

    body = soup.find('body')
    return body.decode_contents() if body else str(soup)
```

Update the chapter route to use `wrap_with_segmenter` instead of `wrap_cjk`:

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
    wrapped = wrap_with_segmenter(content, lang)
    fragment = f'<article data-lang="{lang}">{wrapped}</article>'
    return Response(fragment, mimetype='text/html; charset=utf-8')
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: segmenter-based wrapping with jieba, josa-stripping, and ruby fix"
```

---

### Task 4: Verify frontend uses `data-lookup`

**Files:** `static/app.js`

The Phase 3 dictionary popup already uses `span.dataset.lookup || span.innerText` for lookups. Verify this is correct and add a comment.

**Step 1: Verify in `app.js`**

The click handler should read:

```js
document.getElementById('chapter-container').addEventListener('click', e => {
  const span = e.target.closest('span.w');
  if (span) {
    document.querySelectorAll('span.w.active').forEach(s => s.classList.remove('active'));
    span.classList.add('active');
    // Use data-lookup if present (set by segmenter for josa-stripped Korean
    // and multi-char Chinese compounds). Falls back to innerText for single chars.
    const word = span.dataset.lookup || span.innerText;
    lookupWord(word);
    e.stopPropagation();
    return;
  }
  hidePopup();
});
```

Confirm this was already implemented in Phase 3 (it should be). If the old Phase 1 code used `e.target.textContent`, update it to the above.

**Step 2: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all PASS

**Step 3: Verify in browser**

```bash
python server.py
# Open a Chinese book:
# - Tap 中文 (if jieba segments it as a word) — popup title should show 中文
# - Tap single char 的 — popup title shows 的

# Open a Korean book:
# - Tap 학교에서 — popup lookup should be 학교 (not 학교에서)
# - Check the developer tools Network tab: /api/dict?word=학교
```

**Step 4: Commit**

```bash
git add static/app.js
git commit -m "chore: verified data-lookup handling in tap handler"
```

---

### Task 5: Run full test suite

**Step 1:**

```bash
pytest tests/ -v
```

Expected: all PASS

**Step 2: Performance check**

```bash
# Time a chapter request for a Chinese book:
time curl -s http://localhost:8090/book/your-book/chapter/0 > /dev/null
# Expected: 50–150ms total (jieba contributes ~15–50ms)
```

**Step 3: Final commit**

```bash
git commit -m "chore: verified Phase 4 complete — language intelligence"
```
