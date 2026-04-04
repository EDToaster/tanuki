# Ebook Reader Design: Explorer Synthesis Report

**Date:** 2026-04-03  
**Evaluator:** evaluator  
**Explorers:** completeness, korean, adversarial, progress  
**Design doc reviewed:** `2026-04-03-ebook-reader-design.md`

---

## Overview

Four explorers analyzed the ebook reader design from different angles. This report synthesizes their findings into a prioritized action list, identifies agreements and conflicts, and flags decisions requiring human input.

No findings are truly contradictory — the four reports are complementary, each exposing a different class of problem. Where two explorers independently flagged the same issue, confidence is high.

---

## Priority 1 — Must Fix Before V1

These are bugs or security issues that will cause definite failures in the shipped system.

### 1.1 XSS: Chapter HTML injection via `innerHTML` (Completeness)

**Design doc gap:** The preprocessing pipeline strips `<head>`, internal hrefs, and external stylesheets — but does **not** strip `<script>` tags, `on*` event handlers, or `javascript:` hrefs. The frontend injects the result via `content.innerHTML = html`. A malicious EPUB would execute arbitrary JS in the reader origin.

**Fix:** Add a scrub pass after body extraction (BeautifulSoup is already a dependency):

```python
for tag in soup.find_all(['script', 'style']):
    tag.decompose()
for tag in soup.find_all(True):
    for attr in list(tag.attrs):
        if attr.startswith('on') or (attr == 'href' and str(tag.get('href','')).strip().startswith('javascript:')):
            del tag[attr]
```

**Where:** Design doc §"Chapter Pre-processing Pipeline" (one sentence) + implementation plan Task 6.

---

### 1.2 XSS: Book metadata injected via `innerHTML` in library view (Completeness)

**Design doc gap:** The library card template uses `card.innerHTML = \`...\${book.title}...\${book.author}...\``. An EPUB with a title like `<img src=x onerror=alert(1)>` executes JS.

**Fix:** Use `textContent` and DOM API for all metadata display, not `innerHTML`.

**Where:** Design doc §"Frontend / Library view" (note) + implementation plan Task 8.

---

### 1.3 Path traversal in `book_id` URL parameter (Completeness)

**Design doc gap:** `Path(BOOKS_DIR) / f'{book_id}.epub'` is constructed from a raw Flask route variable. `book_id = '../../../etc/passwd'` resolves outside `BOOKS_DIR`.

**Fix:** Validate `book_id` against `^[a-z0-9_-]+$` at the top of both the cover and chapter route handlers.

**Where:** Design doc §"Backend / Endpoints" (note) + implementation plan Tasks 4 and 6.

---

### 1.4 Image overflow into adjacent pages (Adversarial)

**Design doc gap:** The preprocessing pipeline strips EPUB-internal stylesheets (step 3), removing any `max-width` the EPUB author set on images. Without a reset, images wider than `100vw` bleed into adjacent CSS columns — the next "page" visually.

**Fix:** Apply a fragment reset stylesheet that includes `img { max-width: 100%; }` to all injected chapter HTML.

**Where:** Design doc §"Chapter Pre-processing Pipeline" (note) + implementation plan Task 6.

---

### 1.5 Page count off-by-one on hi-DPI devices (Adversarial)

**Design doc gap:** `Math.round(container.scrollWidth / window.innerWidth)` — `scrollWidth` is an integer but `window.innerWidth` is a float on high-DPI displays. Known cause of blank last pages or phantom extra pages (documented in Readium, epub.js, Thorium).

**Fix:** Use `Math.round((container.scrollWidth + 1) / window.innerWidth)` as a tolerance, or `Math.ceil`. The implementation plan already uses `Math.max(1, ...)` which handles the zero case, but not the rounding error.

**Where:** Design doc §"EPUB Rendering & Pagination" (code snippet) + implementation plan Task 9.

---

### 1.6 Missing Dockerfile (Completeness)

**Design doc gap:** `docker-compose.yml` specifies `build: ./ebook-reader` but the implementation plan has no Dockerfile in the project structure. `docker compose up` fails immediately.

**Fix:** Add a minimal Dockerfile:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV BOOKS_DIR=/books
EXPOSE 8090
CMD ["python", "server.py"]
```

**Where:** Implementation plan (new Task 12, add to project structure).

---

## Priority 2 — Should Add to V1

These findings are not blocking bugs, but omitting them creates significant UX gaps or operational friction.

### 2.1 Korean particle stripping via `data-lookup` attribute (Korean, *confirmed by Adversarial*)

**Design doc gap:** Space-split Korean eojeols almost never match Wiktionary citation forms — nouns appear with particles attached (`학교에서`), verbs appear in conjugated form. Estimated Wiktionary hit rate for bare eojeols: **~15%**. With particle stripping: **~60–70%** for nouns.

**Fix:** Add a server-side particle stripping pass (~30 lines Python, zero new dependencies) in the chapter preprocessing pipeline. Strip the longest-matching josa suffix and store the bare form as `data-lookup` on the span. Frontend uses `span.dataset.lookup || span.innerText` for the API call.

Example output: `<span class="w" data-lookup="학교">학교에서</span>`

**Both explorers agree:** The Korean and Adversarial explorers independently concluded Wiktionary Korean coverage is inadequate for bare eojeols. This gives high confidence that the fix is necessary.

**Where:** Design doc §"Chapter Pre-processing Pipeline" + §"Dictionary Popup" + implementation plan Task 6.

---

### 2.2 Server-side EPUB metadata cache (Completeness)

**Design doc gap:** `/library` parses every EPUB file on every request. For a 50-book library: 50 file reads + 100 XML parses per page load. No caching is mentioned anywhere.

**Fix:** A `dict` keyed by `(epub_path, mtime)` invalidated on file change. ~12 lines, no dependencies.

**Where:** Design doc §"Backend" (new "Caching" subsection) + implementation plan Task 4.

---

### 2.3 Client-side Wiktionary lookup cache (Completeness)

**Design doc gap:** The chapter lookahead buffer caches HTML, but tapping the same character multiple times re-fetches from Wiktionary every time. Wastes bandwidth, risks rate limits.

**Fix:** A `Map<word, {pronunciation, definitions}>` for the session lifetime. Clear on returning to library. ~6 lines.

**Where:** Design doc §"Dictionary Popup" + implementation plan Task 9.

---

### 2.4 Reading progress (localStorage) (Progress)

**Design doc gap:** The design doc marks "reading progress sync across devices" as out of scope but says nothing about *local* progress. Local progress requires zero backend changes and works trivially with `localStorage`.

**Proposed addition:**
- Schema: `progress:{bookId}` → `{ chapterId, pageIndex, updatedAt }`
- Save on every page turn (not `beforeunload`, which is unreliable on mobile iOS)
- Restore: load chapter `chapterId`, navigate to `pageIndex` clamped to `[0, pageCount-1]`
- Library view: render "Ch N · p M" badge on books with saved progress
- `chapterId` is the reliable anchor; `pageIndex` is viewport-dependent and treated as best-effort

**Why this is v1-worthy:** It's ~50 lines of vanilla JS (`progress.js`), zero dependencies, zero backend changes, and addresses the most basic expectation users have of a reading app.

**Where:** Design doc §"Out of Scope (v1)" needs revision + new §"Reading Progress" section in Frontend.

---

### 2.5 Error handling in chapter endpoint (Completeness)

**Design doc gap:** The `/library` endpoint catches exceptions and skips bad EPUBs silently. The chapter endpoint has no error handling — a malformed EPUB returns HTTP 500 with a stack trace.

**Fix:** Wrap chapter extraction in try/except, return 404 for missing books, 500 (or a "chapter unavailable" HTML fragment) for parse failures. Empty chapters should return a sentinel fragment rather than an empty string.

**Where:** Implementation plan Task 6.

---

### 2.6 Flask inconsistency in design doc (Completeness)

**Design doc gap:** Design doc says "Python with the standard library (`http.server` or minimal FastAPI)". Implementation plan uses Flask throughout.

**Fix:** Update design doc to say "Python 3.12, Flask." Flask is the right minimal choice here — `http.server` lacks routing and test-client support; FastAPI adds async complexity.

**Where:** Design doc §"Backend / Stack" (one-line change).

---

## Priority 3 — Defer to V2

These are real improvements but the design is not broken without them, and they carry implementation complexity that should not block v1.

### 3.1 Korean morpheme segmentation (Korean)

Character-level Hangul wrapping would be a regression (Korean syllable blocks are phonetic, not semantic units — confirmed by both Korean and Adversarial explorers). The v1 particle-stripping heuristic handles nouns acceptably. Full verb form handling requires a morphological library.

**Recommendation:** Add `kiwipiepy` (pure Python, no Java/C dependency) in v2. Replace the particle heuristic with proper morpheme segmentation covering all verb forms, irregular conjugations, and stacked particles.

---

### 3.2 Alternative dictionary sources (Adversarial)

Wiktionary is an acceptable starting point but has genuine weaknesses:

| Language | Issue | Alternative |
|----------|-------|-------------|
| Chinese | API returns fragile MediaWiki HTML; format has changed 3x in 5 years | CC-CEDICT (118k entries, 8MB gzip, CC BY-SA, bundleable server-side) |
| Korean | English Wiktionary Korean section is sparse beyond most common 5,000–10,000 words | Korean Basic Dictionary API (국립국어원, ~50k entries, free JSON) |

**Recommendation:** Keep Wiktionary for v1 (it works for Chinese, and the graceful "No result" state handles misses). Consider CC-CEDICT for Chinese and NIKL API for Korean in v2 as the primary quality improvement for the dictionary feature.

---

### 3.3 Chinese word segmentation with jieba (Adversarial)

Character-level Chinese lookup produces correct semantic units for only ~40% of taps. 2-character words (the modal length) return component meanings, not compound meanings (e.g., 中 when the word is 中文). This particularly harms language learners who don't know word boundaries.

**Recommendation:** Server-side `jieba` segmentation at chapter fetch time in v2. Wrap segmented words instead of individual characters. Performance is adequate (~2MB/s on a Raspberry Pi). Deferred because the current design still provides value for ~40% of taps and v1 is about getting the infrastructure right.

---

### 3.4 Annotations (Progress)

The design's choice to exclude annotations from v1 is **confirmed correct** by the progress explorer's analysis:
1. Annotations require stable text-range anchors (XPath + char offsets) that the current design lacks
2. `localStorage` is insufficient at heavy usage (~10k annotations = 5MB limit breached)
3. Rendering highlights back into CSS multi-column is non-trivial

**If added in v2:** Use `IndexedDB` from the start with a hand-rolled ~30-line wrapper. Do NOT start with `localStorage` and migrate later.

---

## Explorer Agreement Map

| Issue | Completeness | Korean | Adversarial | Progress |
|-------|:----------:|:-----:|:-----------:|:-------:|
| Wiktionary Korean coverage | — | ❌ broken | ❌ broken | — |
| Character-level wrapping wrong for Korean | — | ❌ wrong | ❌ (implicit) | — |
| Page count rounding error | ⚠️ (minor note) | — | ❌ bug | ❌ (contextual) |
| Image overflow bug | — | — | ❌ bug | — |
| Security: XSS innerHTML | ❌ two vectors | — | — | — |
| Security: path traversal | ❌ | — | — | — |
| Caching gaps | ❌ two gaps | — | — | — |
| Reading progress design | — | — | — | ✅ full design |
| Annotations: don't add to v1 | — | — | — | ✅ confirmed |
| chapterId as reliable anchor | — | — | — | ✅ |
| Save on page turn not beforeunload | — | — | — | ✅ |

**Strongest signal (2+ explorers):** Korean/Wiktionary, page count rounding.

---

## Decisions Requiring Human Input

### Decision A: Reading progress — add to v1 or leave fully out of scope?

The progress explorer makes a strong case for adding local `localStorage`-based progress in v1 (~50 lines, zero backend changes). The design doc currently implies no progress storage at all. This is a significant UX gap for a reading app.

**Options:**
1. Add reading progress to v1 (recommended — low complexity, high user value)
2. Keep out of scope for v1 and add to v2
3. Add only the "continue reading" restore, without the library badge

---

### Decision B: Korean dictionary source — Wiktionary vs. NIKL API

The particle-stripping fix (§2.1) improves Wiktionary noun coverage from ~15% to ~65%. But Wiktionary Korean lemma coverage itself is sparse beyond the most common ~10,000 words.

**Options:**
1. Keep Wiktionary for v1, add NIKL API (국립국어원) in v2
2. Add NIKL API in v1 as the Korean dictionary source (free, stable JSON, ~50k entries)
3. Accept Wiktionary gaps as tolerable for a personal homelab reader

---

### Decision C: Chinese lookup — character-level (current) vs. server-side segmentation

Character-level lookup is correct for ~40% of taps. The adversarial explorer quantifies the pedagogical harm: learners who don't know word boundaries will look up components, not compounds.

**Options:**
1. Ship character-level in v1, add jieba in v2 (recommended — jieba adds complexity)
2. Add jieba server-side segmentation in v1 for better learner UX
3. Keep character-level forever (sufficient for non-beginner readers)

---

## Findings: Design Doc vs. Implementation Plan

### Design doc should be updated with:
- §"Backend / Stack": Flask (not http.server/FastAPI)
- §"Chapter Pre-processing Pipeline": add script/event-handler stripping, particle stripping for Korean, image reset CSS
- §"Dictionary Popup": add `data-lookup` attribute fallback, mention lookup cache
- §"EPUB Rendering & Pagination": fix page count formula (add tolerance)
- §"Frontend" (new subsection): Reading progress, if Decision A is yes
- §"Infrastructure": reference Dockerfile
- §"Out of Scope": revise if reading progress moves to v1

### Implementation plan only (not design doc):
- Task 4: EPUB metadata cache
- Task 6: Error handling, security scrub, particle stripping code, image reset CSS
- Task 8: DOM API for library cards (not innerHTML)
- Task 9: Page count tolerance, lookup cache
- Task 12 (new): Dockerfile

---

## Summary Score by Explorer

| Explorer | Finding Type | Severity | Actionability |
|----------|-------------|----------|--------------|
| **Completeness** | Gaps, security, infrastructure | High | Highest — concrete fixes for 8 specific issues |
| **Korean** | Linguistics, UX | High | Very high — ~30-line fix with quantified improvement |
| **Adversarial** | Architecture risks, bugs | Medium–High | High — two confirmed bugs, one design risk evaluation |
| **Progress** | Feature design | Medium | High — complete design ready for implementation |

All four explorer reports are high quality and complementary. No explorer findings should be discarded. The completeness report is the highest priority for v1 stability; the Korean report has the highest UX impact per line of code.
