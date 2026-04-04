# Phase 6: Polish + Infrastructure

**Date:** 2026-04-04  
**Phase:** 6 of 6  
**Depends on:** Phase 1 (all endpoints), Phase 3 (dict endpoint), Phase 5 (progress)

**Goal:** Harden the reader with caching, error handling, chapter lookahead, and finalise deployment configuration. No new features — this phase makes everything robust and production-ready for homelab use.

**What this phase adds:**

- Server-side EPUB metadata cache (mtime-keyed dict, ~12 lines, zero deps)
- Client-side dictionary lookup cache (session-scoped `Map`, ~8 lines JS)
- Chapter endpoint error handling (`try/except`, empty chapter sentinel)
- Dictionary error state distinction (network failure vs. word not found)
- Chapter lookahead buffer (prefetch N+1 through N+5, LRU eviction)
- Design doc correction: Flask (not `http.server`/FastAPI)
- Ansible Docker Compose service entry
- Caddy reverse proxy entry

---

### Task 1: Server-side EPUB metadata cache

**Files:** `server.py`, `tests/test_server.py`

The `/library` endpoint currently re-reads and re-parses every EPUB on every request. With a 50-book library this is ~50 file reads + 100 XML parses per page load.

**Step 1: Write failing tests**

```python
from unittest.mock import patch, call
from server import _cached_epub_meta

def test_cached_epub_meta_caches_on_second_call(tmp_path, monkeypatch):
    """parse_epub_metadata is called only once for the same mtime."""
    epub = tmp_path / 'test.epub'
    epub.write_bytes(make_epub_bytes())  # use the make_epub helper from Task 2
    parse_calls = []
    original = __import__('server').parse_epub_metadata
    def counted_parse(data):
        parse_calls.append(1)
        return original(data)
    monkeypatch.setattr('server.parse_epub_metadata', counted_parse)
    _cached_epub_meta(epub)
    _cached_epub_meta(epub)
    assert len(parse_calls) == 1   # second call is a cache hit

def test_cached_epub_meta_invalidates_on_mtime_change(tmp_path, monkeypatch):
    epub = tmp_path / 'test.epub'
    epub.write_bytes(make_epub_bytes())
    _cached_epub_meta(epub)
    # Simulate file replacement — write new bytes (changes mtime)
    epub.write_bytes(make_epub_bytes(title='New Title'))
    meta = _cached_epub_meta(epub)
    assert meta['title'] == 'New Title'
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "cached_epub_meta" -v
```

**Step 3: Implement**

```python
_meta_cache: dict[str, tuple[float, dict]] = {}  # path → (mtime, metadata)

def _cached_epub_meta(epub_path: Path) -> dict | None:
    key   = str(epub_path)
    mtime = epub_path.stat().st_mtime
    if key in _meta_cache and _meta_cache[key][0] == mtime:
        return _meta_cache[key][1]
    try:
        data = epub_path.read_bytes()
        meta = parse_epub_metadata(data)
        meta['chapter_count'] = len(get_chapter_paths(data))
        _meta_cache[key] = (mtime, meta)
        return meta
    except Exception:
        return None
```

Update `/library` to use `_cached_epub_meta`:

```python
@app.route('/library')
def library():
    books = []
    for epub_path in sorted(Path(BOOKS_DIR).glob('*.epub')):
        meta = _cached_epub_meta(epub_path)
        if meta:
            books.append(meta)
    return jsonify(books)
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "cached_epub_meta" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "perf: server-side mtime-keyed EPUB metadata cache"
```

---

### Task 2: Chapter endpoint error handling + empty chapter sentinel

**Files:** `server.py`, `tests/test_server.py`

**Step 1: Write failing tests**

```python
def test_chapter_returns_404_for_unknown_book(client):
    r = client.get('/book/does-not-exist/chapter/0')
    assert r.status_code == 404

def test_chapter_invalid_book_id_returns_400(client):
    r = client.get('/book/../etc/passwd/chapter/0')
    assert r.status_code == 400

def test_chapter_out_of_range_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setenv('BOOKS_DIR', str(tmp_path))
    epub_bytes = make_epub_bytes(chapters=2)
    (tmp_path / 'test-book.epub').write_bytes(epub_bytes)
    r = client.get('/book/test-book/chapter/99')
    assert r.status_code == 404

def test_empty_chapter_returns_sentinel(client, tmp_path, monkeypatch):
    """If preprocessing produces empty content, return a sentinel fragment."""
    monkeypatch.setenv('BOOKS_DIR', str(tmp_path))
    (tmp_path / 'empty-book.epub').write_bytes(make_epub_bytes(empty_chapter=True))
    r = client.get('/book/empty-book/chapter/0')
    assert r.status_code == 200
    assert b'chapter-empty' in r.data
```

**Step 2: Run to confirm failure**

```bash
pytest tests/test_server.py -k "chapter" -v
```

**Step 3: Implement**

Update the chapter route:

```python
@app.route('/book/<book_id>/chapter/<int:index>')
def chapter(book_id, index):
    _validate_book_id(book_id)   # aborts 400 on invalid id
    epub_path = Path(BOOKS_DIR) / f'{book_id}.epub'
    if not epub_path.exists():
        abort(404)
    try:
        data    = epub_path.read_bytes()
        content = extract_chapter(data, index)
    except IndexError:
        abort(404)   # chapter index out of range
    except Exception:
        abort(500)
    if content is None:
        abort(404)
    if not content.strip():
        content = '<p class="chapter-empty">Chapter has no readable content.</p>'
    lang = _get_epub_lang(data)
    return f'<article data-lang="{lang}">{content}</article>'
```

**Step 4: Run tests**

```bash
pytest tests/test_server.py -k "chapter" -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "fix: chapter endpoint error handling — 400/404/500 + empty chapter sentinel"
```

---

### Task 3: Client-side dictionary lookup cache

**Files:** `static/app.js`

Tapping the same word multiple times re-fetches `/api/dict` each time. A session-scoped `Map` eliminates redundant requests.

**Step 1: Add cache around `lookupWord`**

In `app.js`, add a module-level cache and wrap the lookup function:

```js
const dictCache = new Map();  // word → normalized entry or null (not-found)

async function lookupWord(word, lang) {
  const cacheKey = `${lang}:${word}`;

  if (dictCache.has(cacheKey)) {
    renderPopup(dictCache.get(cacheKey), word);
    return;
  }

  showPopupLoading(word);

  let entry = null;
  try {
    const r = await fetch(`/api/dict?word=${encodeURIComponent(word)}&lang=${encodeURIComponent(lang)}`);
    if (r.ok) {
      entry = await r.json();
      if (entry.not_found) entry = null;
    }
  } catch {
    renderPopupError(word, 'Network error — check connection.');
    return;
  }

  dictCache.set(cacheKey, entry);  // cache null for not-found too (avoids re-fetch)
  renderPopup(entry, word);
}

// Clear cache when returning to library (stale entries across reading sessions)
function closeReader() {
  dictCache.clear();
  // ... existing close logic ...
}
```

**Step 2: Distinguish network errors from "not found" in `renderPopup`**

```js
function renderPopup(entry, word) {
  if (!entry) {
    showPopupNotFound(word);
    return;
  }
  // ... render readings and definitions from entry ...
}

function showPopupNotFound(word) {
  // "No entry found" + "Open in source ↗" link
  popupEl.querySelector('.popup-word').textContent = word;
  popupEl.querySelector('.popup-body').innerHTML = '';
  const msg = document.createElement('p');
  msg.className = 'popup-no-result';
  msg.textContent = 'No entry found.';
  popupEl.querySelector('.popup-body').appendChild(msg);
  openPopup();
}

function renderPopupError(word, message) {
  popupEl.querySelector('.popup-word').textContent = word;
  popupEl.querySelector('.popup-body').innerHTML = '';
  const msg = document.createElement('p');
  msg.className = 'popup-error';
  msg.textContent = message;
  popupEl.querySelector('.popup-body').appendChild(msg);
  openPopup();
}
```

**Step 3: Commit**

```bash
git add static/app.js
git commit -m "perf: client-side dictionary lookup cache (session-scoped Map)"
```

---

### Task 4: Chapter lookahead buffer

**Files:** `static/app.js`

On entering chapter N, silently prefetch chapters N+1 through N+5. Store in a `Map`. Chapter transitions become instant — swap `innerHTML` and recalculate page count. Evict chapters more than 2 behind the current position.

**Step 1: Implement**

```js
const chapterCache = new Map();   // chapterIndex → htmlString
const LOOKAHEAD    = 5;
const EVICT_BEHIND = 2;

async function prefetchChapters(profile, bookId, currentIndex, totalChapters) {
  for (let i = currentIndex + 1; i <= Math.min(currentIndex + LOOKAHEAD, totalChapters - 1); i++) {
    if (chapterCache.has(i)) continue;
    fetch(`/book/${bookId}/chapter/${i}`)
      .then(r => r.text())
      .then(html => chapterCache.set(i, html))
      .catch(() => {});  // prefetch failures are silent
  }
  // Evict stale entries
  for (const [idx] of chapterCache) {
    if (idx < currentIndex - EVICT_BEHIND) chapterCache.delete(idx);
  }
}

async function loadChapter(profile, bookId, index, totalChapters) {
  let html;
  if (chapterCache.has(index)) {
    html = chapterCache.get(index);
  } else {
    const r = await fetch(`/book/${bookId}/chapter/${index}`);
    html = await r.text();
    chapterCache.set(index, html);
  }

  contentEl.innerHTML = html;
  currentPage  = 0;
  totalPages   = Math.max(1, Math.round((contentEl.scrollWidth + 1) / window.innerWidth));
  renderPage();

  // Trigger lookahead after render
  prefetchChapters(profile, bookId, index, totalChapters);
}

// Clear chapter cache when leaving the reader
function closeReader() {
  chapterCache.clear();
  dictCache.clear();
}
```

**Step 2: Commit**

```bash
git add static/app.js
git commit -m "feat: chapter lookahead buffer (N+1 to N+5 prefetch, LRU eviction)"
```

---

### Task 5: Update design doc — Flask, not `http.server`/FastAPI

**Files:** `docs/2026-04-03-ebook-reader-design.md`

The design doc currently reads:

> Python with the standard library (`http.server` or minimal FastAPI). No ORM, no database.

Update to:

> **Stack:** Python 3.12, Flask. No ORM, no database — EPUBs on disk are the source of truth.

**Step 1: Edit**

Find and replace the inconsistent line in the design doc.

**Step 2: Commit**

```bash
git add docs/2026-04-03-ebook-reader-design.md
git commit -m "docs: clarify backend stack is Flask (not http.server/FastAPI)"
```

---

### Task 6: Docker Compose service + Caddy entry

**Files:** `roles/media-stack/templates/docker-compose.yml.j2`, `roles/media-stack/vars/main.yml`

**Step 1: Full Docker Compose service entry**

```yaml
ebook-reader:
  build: ./ebook-reader
  container_name: ebook-reader
  volumes:
    - {{ media_path }}/books:/books:ro
    - {{ data_path }}/ebook-reader:/data
  ports:
    - "8090:8090"
  restart: unless-stopped
  environment:
    - BOOKS_DIR=/books
    - DB_PATH=/data/progress.db
```

**Step 2: Caddy entry**

Add to `caddy_sites` in vars:

```yaml
caddy_sites:
  - { name: books, port: 8090, host: "{{ media_stack_ip }}" }
```

This exposes the reader at `http://books.homelab.local`.

**Step 3: Remove Kavita and Calibre-Web**

Once the reader is verified working:

```yaml
# Remove or comment out:
# kavita:
#   image: kizaing/kavita:latest
#   ...
# calibre-web:
#   image: linuxserver/calibre-web:latest
#   ...
```

**Step 4: Commit**

```bash
git add roles/
git commit -m "feat: Docker Compose + Caddy entries, remove Kavita and Calibre-Web"
```

---

### Summary

After Phase 6, the reader is complete:

| Concern | Before | After |
|---------|--------|-------|
| `/library` performance | 50 file reads per request | Cached, only re-parses on file change |
| Dict lookups | Network on every tap | Session-cached, zero re-fetches |
| Chapter load | Network on every turn | Instant for pre-fetched chapters |
| Chapter errors | Unhandled 500 | 400/404/500 + user-facing sentinel |
| Dict errors | All collapsed to "No result" | Network error vs. word not found distinguished |
| Deployment | Incomplete | Full Docker Compose + Caddy, Kavita/Calibre-Web removed |
