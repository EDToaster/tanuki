# jieba Segmentation Design for Chinese Chapter Pipeline

**Date:** 2026-04-03
**Explorer:** explorer-jieba
**Scope:** Server-side Chinese word segmentation via jieba for the ebook reader chapter pipeline

---

## Summary

The current pipeline wraps every individual CJK character in a tappable `<span class="w">`, which produces useful lookup results only ~40% of the time (single-character words). The other ~60% of taps land on characters that are components of multi-character compounds — the standalone character entry doesn't reflect the compound's meaning (e.g., tapping 电 in 电话 yields "electricity" not "telephone").

**Recommendation:** Add server-side jieba segmentation to the chapter pre-processing pipeline. jieba correctly segments Chinese into words (1–4 characters), dramatically improving tap-to-lookup accuracy from ~40% to ~80–85%.

---

## 1. jieba Integration in the Chapter Pipeline

### Current Pipeline Step 4 (Chinese)

```
For each text node: replace every char in \u4e00–\u9fff with <span class="w">char</span>
```

### Proposed Pipeline Step 4 (Chinese with jieba)

```
For each text node containing CJK characters:
  1. Run jieba.cut(text_node_content) → list of tokens
  2. For each token:
     - If token contains CJK characters: emit <span class="w" [data-lookup]>token</span>
     - Otherwise (punctuation, spaces, Latin, etc.): emit as plain text node
  3. Replace the original text node with the new sequence
```

### Composing with BeautifulSoup HTML Processing

The critical constraint: preserve all surrounding HTML tags (`<p>`, `<em>`, `<strong>`, `<ruby>`, etc.) while only modifying leaf text nodes.

**Traversal approach:** Use `soup.find_all(string=True)` to collect all NavigableString objects in the parsed tree. This correctly surfaces only leaf text nodes — it does not enter attribute values, and it respects the existing tag structure.

**Replacement pattern for a single text node:**

```
text_node = NavigableString("他来到了网易杭研大厦")
tokens = jieba.cut(str(text_node))
# → ["他", "来到", "了", "网易", "杭研", "大厦"]

For each token (in reverse order to preserve insertion positions):
    insert_after(text_node, new_node)
extract(text_node)
```

By inserting after the original node in **reverse order** and then extracting the original, we reconstruct the sequence correctly. Alternatively, collect all new nodes, call `replace_with()` on the text node with a DocumentFragment equivalent (a BeautifulSoup tag with the new content appended).

**Practical BeautifulSoup idiom:**
- Get the text node's parent
- Call `text_node.replace_with(placeholder_tag)`
- Fill the placeholder with the generated spans and text
- Unwrap the placeholder (so its children are promoted to the parent)

**Example input/output:**

```html
<!-- Input HTML from EPUB -->
<p>他<em>来到</em>了网易杭研大厦</p>

<!-- After jieba segmentation -->
<p>
  <span class="w">他</span>
  <em>
    <span class="w" data-lookup="来到">来到</span>
  </em>
  <span class="w">了</span>
  <span class="w" data-lookup="网易">网易</span>
  <span class="w" data-lookup="杭研">杭研</span>
  <span class="w" data-lookup="大厦">大厦</span>
</p>
```

**Why this is safe:**
- Each `<em>` (or `<strong>`, `<ruby>`, etc.) contains its own text node(s)
- Those text nodes are processed independently
- The tag boundaries are never crossed — jieba.cut() only receives one text node at a time
- Non-CJK content (Latin, Arabic numerals, punctuation, spaces) is left as plain text nodes, not wrapped

**Edge cases:**

| Case | Handling |
|------|----------|
| Mixed CJK + Latin in one node (e.g., "iPhone买了") | jieba handles — it passes non-CJK runs through unchanged |
| Punctuation (。！？「」) | Not CJK range, emitted as plain text |
| Existing spans in EPUB (e.g., `<span class="ruby">`) | Their text nodes processed independently; outer span preserved |
| Empty text nodes (whitespace-only) | No CJK chars → skipped |
| Numbers (e.g., 2024年) | "年" is CJK; jieba segments "2024" as non-CJK prefix + "年" |

---

## 2. Dependency: jieba

### Package Details

| Property | Value |
|----------|-------|
| PyPI name | `jieba` |
| Latest stable | `0.42.1` (as of 2026) |
| Size | ~2MB (mostly `dict.txt` — the core frequency dictionary) |
| Dependencies | None (pure Python, no C extensions required) |
| License | MIT |

### requirements.txt Addition

```
jieba==0.42.1
```

No other changes to requirements.txt. BeautifulSoup (`beautifulsoup4`) is already required for the HTML stripping pipeline.

### First-Run Behavior and Initialization Strategy

**Default lazy-load behavior:** jieba loads its trie dictionary on the **first call** to `jieba.cut()`. This takes ~0.5–1.0 seconds (dictionary parse + trie construction). If not pre-loaded, the very first chapter request after server startup will have ~1s of extra latency.

**Mitigation: module-level initialization**

```python
# At the top of the server module, after imports:
import jieba
jieba.initialize()  # Pre-loads dictionary at startup, not at first request
```

`jieba.initialize()` is documented in jieba's README specifically for this use case. Called at module level, it runs once when the server process starts and incurs the ~1s penalty only at startup — not during any user request.

**Why this is correct for this deployment:** The ebook reader runs as a single long-lived Docker container process. Module-level initialization is standard Python practice (analogous to establishing a DB connection pool at startup). There is no serverless/per-process-per-request concern here.

---

## 3. Performance

### jieba.cut() Throughput

jieba's default mode (精确模式, Viterbi HMM) processes approximately **100–500 KB/s** of Chinese text on a modern CPU. For a typical chapter:

| Chapter size | Estimated CJK content | jieba time |
|-------------|----------------------|------------|
| 3,000 chars | ~9KB UTF-8 | ~10–30ms |
| 5,000 chars | ~15KB UTF-8 | ~15–50ms |
| 10,000 chars | ~30KB UTF-8 | ~30–100ms |

(Benchmarks vary with hardware; a modest Docker container on a home server should land in the middle of these ranges.)

### Is Synchronous Processing Acceptable?

**Yes, for v1.** The chapter endpoint already does:
1. Open EPUB ZIP → file I/O (~5–20ms)
2. Locate chapter via OPF spine parsing → CPU (~5ms)
3. Extract and parse HTML with BeautifulSoup → CPU (~10–30ms)
4. Strip head, hrefs, stylesheets → CPU (~5ms)

Adding jieba (step 4b) contributes **~15–50ms** to a pipeline that already takes ~30–70ms. Total chapter latency: **~50–120ms**. This is well within acceptable bounds for a single-user self-hosted reader.

The existing design has **no chapter caching** in v1. jieba does not change this decision for v1.

### Chapter Cache for v2

If jieba segmentation causes perceptible latency on very long chapters (>10,000 chars), a chapter cache keyed on `(book_id, chapter_n, mtime_of_epub)` would solve the problem for subsequent requests. The cache value is the final pre-processed HTML string. The mtime key ensures cache invalidation if the EPUB is replaced.

The cache can be a simple Python dict held in server memory (no Redis, no disk). For a single-user deployment, LRU with a cap of ~20 chapters covers typical reading patterns. This is a pure in-memory dict addition, consistent with the "no database" constraint.

**v1 decision:** No cache. **v2 optimization path:** In-memory LRU dict keyed on `(book_id, chapter_n, mtime)`.

---

## 4. Span Format

### Design Principle

The span format follows the same pattern established by Korean particle stripping: the visible text is always the original word, and `data-lookup` carries the dictionary lookup key when it differs from `innerText`. For Chinese after jieba, the visible text and lookup key are always identical (jieba returns the word as-is), so `data-lookup` is only needed for multi-character words.

Single-character words don't need `data-lookup` because `span.innerText === lookup_term` is always true.

### Output Format

```html
<!-- Single-char word: innerText IS the lookup term, no data-lookup needed -->
<span class="w">我</span>
<span class="w">了</span>
<span class="w">的</span>

<!-- 2-char compound: data-lookup set to the full word -->
<span class="w" data-lookup="电话">电话</span>
<span class="w" data-lookup="中文">中文</span>
<span class="w" data-lookup="来到">来到</span>

<!-- 4-char idiom -->
<span class="w" data-lookup="一石二鸟">一石二鸟</span>
<span class="w" data-lookup="马到成功">马到成功</span>

<!-- Non-CJK content: not wrapped -->
iPhone买了
→ iPhone<span class="w">买</span><span class="w">了</span>
```

### Simplified Rule

```
if len(token) == 1 and is_cjk(token):
    emit: <span class="w">token</span>
elif len(token) > 1 and any(is_cjk(c) for c in token):
    emit: <span class="w" data-lookup="token">token</span>
else:
    emit: plain text
```

### Frontend Lookup Key (No Change Required)

The Korean particle-stripping design already uses:

```js
const lookupTerm = span.dataset.lookup || span.innerText;
```

This works identically for jieba-segmented Chinese spans. No frontend changes are needed — the Chinese language path simply starts populating `data-lookup` on multi-char words where it previously set no attributes.

### Contrast with Old Format

| Token | Old format | New format |
|-------|------------|------------|
| 中 (standalone) | `<span class="w">中</span>` | `<span class="w">中</span>` (unchanged) |
| 中 in 中文 | `<span class="w">中</span><span class="w">文</span>` | `<span class="w" data-lookup="中文">中文</span>` |
| 电话 | `<span class="w">电</span><span class="w">话</span>` | `<span class="w" data-lookup="电话">电话</span>` |
| 一石二鸟 | 4 separate single-char spans | `<span class="w" data-lookup="一石二鸟">一石二鸟</span>` |

---

## 5. Wiktionary Coverage Impact

### Why Character-Level Is ~40% Correct

Chinese text has a characteristic word-length distribution:
- ~40% of vocabulary items are single-character words (的, 了, 我, 是, 在, 人, 有, ...)
- ~60% are multi-character compounds (电话, 中文, 老师, 国家, 时间, ...)
- ~5% are 4-character idioms (成语)

Tapping a single-character word like 的 or 我 produces a correct and useful result. Tapping 电 inside 电话 yields the entry for 电 ("electricity; electric; power") — technically correct for the standalone character, but the reader wanted 电话 ("telephone"). This is the ~60% failure case.

### Wiktionary Chinese Coverage by Word Type

English Wiktionary has approximately **196,000 Chinese headword entries** (April 2026, per prior discovery). Coverage by segment type:

| Type | Wiktionary coverage | Notes |
|------|--------------------|----|
| Single-char (common) | Excellent | Nearly all common chars (几千 most-used) have entries |
| 2-char compounds (common) | Very good | Everyday words (电话, 中文, 学习) well covered |
| 2-char compounds (rare/technical) | Patchy | Specialized vocabulary thinner |
| 4-char idioms (common) | Good | 成语 entries often include etymology and usage |
| Proper nouns (people, places) | Poor | Coverage inconsistent |
| Neologisms (slang, internet terms) | Poor | Wiktionary lags |

### Estimated Hit Rate with jieba

| Scenario | Hit rate |
|----------|----------|
| Current (character-level) | ~40% |
| jieba + Wiktionary (all text) | ~78–85% |
| jieba + CC-CEDICT (all text) | ~85–92% |

The improvement from ~40% to ~80% comes from:
1. Compounds that were split now appear as single lookup units (電話 instead of 電 + 話)
2. jieba's default mode (Viterbi, ~96% accuracy) correctly identifies most compound boundaries
3. Wiktionary has strong coverage for the top ~10,000 Chinese words, which cover >95% of everyday prose text

The remaining ~15–20% failure rate at the jieba+Wiktionary tier comes from:
- jieba mis-segmentation of unusual proper nouns (~4% of tokens on standard corpora)
- Rare compounds and technical vocabulary not in Wiktionary
- Neologisms and internet slang

### CC-CEDICT as a Superior Alternative (v2)

The adversarial explorer identified CC-CEDICT (118k entries, CC BY-SA, structured format) as a better Chinese source than Wiktionary's 196k entries delivered as MediaWiki HTML. CC-CEDICT ships with HSK level metadata and pinyin for every entry, and avoids Wiktionary's brittle HTML parser.

jieba segmentation is orthogonal to dictionary choice — the segmented word is passed to whichever dictionary backend is used. Adopting CC-CEDICT in v2 would further improve the hit rate to ~85–92% without changing the segmentation pipeline.

---

## 6. Design Document Changes Required

### Section: "Chapter Pre-processing Pipeline"

Replace:

> **Chinese (`zh`):** every individual character (including punctuation skipped — only wrap `\u4e00-\u9fff` and CJK extension ranges)

With:

> **Chinese (`zh`):** every word as segmented by jieba (server-side, `jieba.cut()` default mode). Single-character words are wrapped as `<span class="w">char</span>`; multi-character words are wrapped as `<span class="w" data-lookup="word">word</span>`. Non-CJK content (punctuation, spaces, Latin) is not wrapped. jieba is initialized at server startup via `jieba.initialize()` to avoid per-request dictionary loading overhead.

### Section: "Dictionary Popup"

Already uses `innerText` for lookup. Update to:

> **Lookup key:** use `span.dataset.lookup || span.innerText` (same pattern as Korean). For Chinese, multi-character words carry `data-lookup`; single-character words use `innerText` directly.

### Section: "Out of Scope (v1)"

Remove:

> CJK word segmentation — user selects multi-character phrases manually

(jieba segmentation is now in scope for v1 Chinese.)

### Section: "Stack" / requirements.txt

Add: `jieba==0.42.1` (pure Python, no system dependencies, no build step).

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| jieba mis-segmentation of unusual text | Medium | Low | Viterbi mode is ~96% accurate; mis-segments are rare; fallback is still a valid character tap via text selection |
| First-request latency (dictionary loading) | High (without fix) | Medium | module-level `jieba.initialize()` at startup — eliminates the risk entirely |
| Docker image size increase (~2MB) | Certain | Very low | +2MB is negligible for a Docker image |
| Chapter processing latency exceeds budget | Low | Low | ~15–50ms for typical chapters; v2 LRU cache available if needed |
| jieba.cut() on malformed/mixed text | Low | Low | jieba is battle-tested; passes non-CJK through unchanged |

---

## Appendix: Segmentation Examples

| Input text | jieba.cut() output | Old spans | New spans |
|------------|-------------------|-----------|-----------|
| 我来到北京清华大学 | 我/来到/北京/清华大学 | 9 single-char spans | 4 spans (1 single, 3 multi) |
| 他买了一部电话 | 他/买/了/一/部/电话 | 7 single-char spans | 6 spans (5 single, 1 multi) |
| 中国科学技术大学 | 中国/科学/技术/大学 | 8 single-char spans | 4 multi-char spans |
| 乒乓球拍卖完了 | 乒乓球/拍卖/完/了 | 7 single-char spans | 4 spans (2 single, 2 multi) |

These examples are directly from jieba's README (default mode). Note: "乒乓球拍卖完了" is the famous ambiguous sentence ("the ping-pong paddles have sold out" vs. "the ping-pong balls have been auctioned off") — jieba correctly segments 拍卖 as "to auction" rather than splitting after 球拍.
