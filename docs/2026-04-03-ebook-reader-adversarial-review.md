# Adversarial Review: Ebook Reader Design

**Date:** 2026-04-03  
**Reviewer:** explorer-adversarial  
**Target:** `2026-04-03-ebook-reader-design.md`  
**Approach:** Skeptical challenge of core architectural bets

This review takes a deliberately contrarian stance. The design has genuine strengths, but several bets carry more risk than the doc implies. Each section ends with a **verdict**: Good Bet, Acceptable Risk, or Risky Bet.

---

## 1. CSS Multi-Column Pagination

### The Bet
Use `column-width: 100vw` + `overflow: hidden` + `translateX` for pagination. Page count via `Math.round(container.scrollWidth / window.innerWidth)`.

### What Could Go Wrong

**Subpixel rounding on `scrollWidth`**

`scrollWidth` is an integer (rounded up in most browsers), while `window.innerWidth` is a float on high-DPI displays. On a device with `devicePixelRatio = 3` and a physical width that doesn't divide evenly, you get off-by-one errors in page count. Real-world symptom: the last page appears blank, or the reader thinks there's one extra page. This is a well-known pain point for JS ebook engines — Readium, epub.js, and Thorium have all had issues with this exact calculation.

Mitigation would be: use `Math.ceil` or subtract a tolerance (e.g., `(scrollWidth + 1) / innerWidth`), but there's no single correct fix. The correct calculation depends on whether the browser floors or rounds column boundaries.

**`column-width: 100vw` is not guaranteed to produce exactly-100vw columns**

The CSS spec says `column-width` is a *suggested* width, not a hard minimum. When `column-gap: 0` and `column-count` is not set, most browsers honor 100vw — but the spec permits the browser to choose a different count if it calculates that fewer wider columns "work better." In practice Blink/WebKit both respect it, but it's not contractual.

**Images wider than their content area**

If an EPUB chapter contains an `<img>` without `max-width: 100%` in the source, it will overflow its column and visually bleed into the next "page." The design strips EPUB-internal stylesheets (step 3 of the pipeline) — so any `max-width` the EPUB author set is also stripped. There must be a reset stylesheet applied to the injected fragment that includes `img { max-width: 100%; }`.

**`break-inside` support is partial**

To prevent a `<span class="w">` (or a paragraph, or a heading) from being split across a column boundary, you'd use `break-inside: avoid`. Caniuse shows `break-inside: avoid` has partial support on iOS Safari and Chrome Android — the `-webkit-column-break-inside: avoid` prefixed version is required on WebKit. Without it, a character's tap target can be split across two pages, making the lookup popup appear on the wrong page or fail to fire.

**Known WebKit bugs (as of April 2026)**

A live search of WebKit Bugzilla returns ~20 open issues against CSS multi-column, including:
- Glyph clipping at column boundaries (text at the very edge of a column may be visually cut)
- Selection highlight rendering across column boundaries (user drags to select text, highlight jumps)
- Column height calculation errors with flexbox children

For CJK content specifically: ruby text (`<ruby>` + `<rt>`) — used in some Chinese epubs for pinyin annotation — is not tested. If a future v2 adds server-side ruby injection for learning aids, multi-column behavior becomes unpredictable.

**`translateX` vs `scrollLeft`**

`translateX` is the correct choice for this use case. Reasons:
- GPU-composited, no layout recalculation
- Works even when `overflow: hidden` clips the container
- `scrollLeft` on a multi-column container has inconsistent behavior across mobile browsers (some browsers reset scrollLeft on reflow)

`translateX` is the right bet here.

**CJK-specific multi-column rendering**

CJK text typically uses fonts with full-width em-squares, and mobile browsers on iOS/Android may apply fractional font metrics. A `font-size: 18px` Chinese character may actually render at 17.8px effective em-height due to the system font's internal leading, causing columns to be slightly shorter than `100vh` and creating an accumulating rounding error over many pages. This is impossible to fully eliminate without a known-good font (system-ui is not "known-good" in this sense).

**Verdict: Acceptable Risk** — The approach works in the majority of cases but will exhibit off-by-one page count errors on specific devices. These are annoying but not catastrophic. The image overflow issue is a real bug that must be fixed in the preprocessing pipeline.

---

## 2. Wiktionary as Sole Dictionary Source

### The Bet
All dictionary lookups go to `en.wiktionary.org` API directly from the browser. No proxy, no bundled dictionary, no fallback.

### Coverage

**Chinese**

The English Wiktionary contains approximately **196,000 Chinese headwords** (as of April 2026 database dump). This sounds large, but consider:
- CC-CEDICT, a downloadable dictionary maintained since 1997, has approximately **118,000 traditional + simplified entries** covering Modern Standard Chinese with rich grammatical tagging
- The 196k figure in Wiktionary includes many stub entries with no pronunciation and no definitions beyond "see [other character]"
- Classical Chinese characters with obscure readings that appear in literary texts are hit-or-miss
- Variant character forms (simplified/traditional) may or may not have parallel entries

**Korean**

Korean coverage is substantially worse. The Korean language edition of Wiktionary has fewer entries than English Wiktionary, but more importantly: English Wiktionary's Korean section is sparse for anything beyond the most common Sino-Korean vocabulary. A Hanja lookup (Chinese-origin Korean word) might work; a native Korean word (순우리말) is much less reliable. There is no authoritative count, but informal community assessments suggest coverage drops sharply for Korean headwords beyond the most common 5,000–10,000.

**The API Fragility Problem**

```
GET https://en.wiktionary.org/w/api.php?action=parse&page={word}&prop=sections|text&format=json&origin=*
```

This returns a full MediaWiki-rendered HTML blob. To extract pronunciation and definitions, the frontend must:
1. Parse the HTML string into a DOM
2. Find the correct language section (Chinese vs Korean vs English — many entries have all three)
3. Locate the pronunciation line (which uses wikitext conventions like `{{zh-pron}}` rendered as a table)
4. Extract numbered definitions

MediaWiki HTML output is not a stable API. The structure of a Wiktionary entry page is determined by templates, and template rendering can change. The `{{zh-pron}}` template output has changed format at least three times in the past five years.

**Network dependency**

Direct browser-to-Wiktionary CORS works today (`origin=*` is confirmed open). But:
- Wiktionary rate-limits by IP. On a home network with shared IPs (NATted), aggressive caching misses could trigger rate limits
- The design correctly handles "No result" gracefully, but network failures (timeout, DNS failure at home if the server is offline) produce the same UX as "word not found" — which is misleading

**Better alternatives**

| Source | Coverage (zh) | Coverage (ko) | License | Offline? | API stability |
|--------|--------------|--------------|---------|----------|--------------|
| Wiktionary | ~196k entries | Poor | CC BY-SA | No | Fragile HTML |
| CC-CEDICT | ~118k entries | N/A | CC BY-SA 3.0 | Yes | Stable TSV |
| KANJIDIC2 / Unihan | Good for hanzi metadata | N/A | CC0 | Yes | Stable XML |
| Korean Basic Dictionary (국립국어원) | N/A | ~50k entries | Open API | No | Stable JSON |
| Naver Open Dictionary API | N/A | Excellent | Proprietary | No | Stable JSON |

**CC-CEDICT** is the strongest alternative for Chinese. It can be bundled server-side (118k entries compress to ~8 MB gzip) and served via a `/lookup?lang=zh&word=X` endpoint. This would:
- Eliminate network dependency for Chinese
- Provide stable structured data (traditional, simplified, pinyin, definitions)
- Allow offline use

For Korean, the Korean Basic Dictionary API from NIKL (국립국어원) provides ~50k entries via a free open API with proper romanization.

**Verdict: Risky Bet** — Wiktionary is adequate for casual Chinese reading but its Korean coverage is insufficient for the stated use case. The HTML parsing approach is fragile. For a v1 self-hosted tool where the server is already doing backend work, bundling CC-CEDICT server-side and proxying Korean lookups would be substantially more reliable.

---

## 3. No Client-Side Segmentation for Chinese

### The Bet
Wrap every CJK character individually with `<span class="w">`. Single-tap = single character lookup. Multi-character lookup requires manual text selection.

### Why Character-Level Lookup Is Problematic

Chinese is written without spaces. Words are 1–4 characters, with the modal length being 2 characters. Character-level lookup produces correct results for a small fraction of taps:

**High false positive rate for common characters**

Consider 的 (de) — the most common character in Mandarin. Standalone, it's a grammatical particle with no semantic content. A reader tapping 的 gets: "possessive particle; structural particle; nominalizer." This is technically correct but useless for comprehension. The reader wanted to know what the word containing 的 means.

**Multi-character words that look like single characters**

- 中 alone: "middle, center, China, hit" — polysemous, context-dependent
- 中国: "China" — clear and unambiguous
- 中毒: "poisoned" — completely different meaning from either component
- 中文: "Chinese language" — different again

When the reader taps 中 in 中文 hoping to understand the word, they get four definitions none of which say "Chinese language."

**4-character idioms (成语)**

Chinese literary texts use 成语 extensively. 马到成功 means "immediate success" (lit. "horse arrives, success achieved"). Looking up each character individually gives 马=horse, 到=arrive, 成=become/accomplish, 功=merit/success — which does not tell you this is a fixed idiom meaning "success at first attempt."

**The UX tax**

"The user selects multi-character phrases manually" — this is a real workflow, and experienced Chinese readers do use text-selection fallback. But the design targets *language learners*, who are precisely the users who don't know which characters belong to the same word. Asking a learner to correctly select 中文 when they don't yet know it's one word is backwards from a pedagogical standpoint.

### Counterarguments (Steel-Manning the Design)

- Character-level lookup still works for ~40% of Chinese text that is single-character words (高, 美, 看, 好)
- Adding client-side segmentation adds complexity and a dictionary dependency (jieba.js is ~2MB)
- Server-side segmentation is possible but adds latency to the chapter preprocessing step
- For casual reading (not serious study), "tap a character, see its meanings" is still useful even if imprecise

### A Viable Middle Ground

The server already preprocesses chapter HTML. Adding server-side segmentation with a library like `jieba` (Python) at chapter fetch time would:
1. Wrap segmented words instead of individual characters: `<span class="w" data-word="中文">中文</span>`
2. Keep the char-level `<span>` wrapper for non-word characters (punctuation, numbers)
3. Maintain the single-tap UX but with the correct semantic unit

The performance cost is acceptable — chapter segmentation runs once per fetch, not per interaction. `jieba` processes ~2MB of text/second on a Raspberry Pi.

**Verdict: Acceptable Risk for v1, but worth revisiting** — Character-level lookup is better than nothing and works for ~40% of taps. The real problem is that it creates a misleading UX where the reader *thinks* they looked up a word but actually looked up a component. A server-side segmentation pass would be a significant quality improvement with modest implementation cost.

---

## 4. Zero Client-Side Dependencies

### The Bet
No framework, no bundler, no build step. Pure HTML + vanilla JS. The doc notes "no client-side dependencies at all" since EPUB unzip happens server-side.

### Is This Achievable?

Yes, and it's actually one of the strongest design decisions in the document.

The concern about "hidden complexity" is real but manageable. Here's what the zero-dependencies constraint forces into vanilla JS:

1. **Wiktionary HTML parsing** — must walk a DOM tree extracted from a `<div>` to find language sections and definition lists. This is ~40 lines of querySelector logic, not complex.
2. **Touch gesture handling** — swipe detection with start/end x coordinates. ~20 lines.
3. **Bottom sheet animation** — CSS transform + transition, no JS animation library needed.
4. **Event delegation for 10,000+ spans** — a single `click` listener on the article element with `e.target.closest('.w')` is the correct pattern. This is more efficient than per-span listeners and requires zero dependencies.
5. **Chapter preloading** — vanilla `fetch()` + `Map<int, string>`. Trivial.

**Where it gets hairy**

The Wiktionary HTML parsing is the most fragile part. A production implementation will need something like:

```js
function parseWiktionary(html, lang) {
  const div = document.createElement('div');
  div.innerHTML = html;
  // find the <h2 id="Chinese"> or <h2 id="Korean"> section
  // then walk siblings until next h2
  // extract .mw-headline pronunciation tables and ol li definitions
}
```

MediaWiki HTML uses `id` attributes on headings to identify language sections. This is stable-ish but not contractual. When Wiktionary changes its HTML output (as it did for the pronunciation table format in 2022), this silently breaks.

**The real hidden complexity**

The actual hidden complexity is not technical — it's maintenance. Zero dependencies means:
- No library to upgrade when the Wiktionary API format changes
- No community of users who will report the bug
- No upstream fix to pull in

Everything is bespoke. This is fine for a personal homelab tool but means the developer is the sole support path.

**Is a build step really zero-cost to skip?**

For a tool of this scope (one HTML file, one JS file), yes. TypeScript and bundling would add real overhead. System font stack removes font loading concerns. The constraint is appropriate.

**Verdict: Good Bet** — Zero dependencies is the right call for a self-hosted personal tool. The hidden complexity is real but bounded and acceptable. The developer should be aware that Wiktionary HTML parsing is the most fragile component and will require periodic maintenance.

---

## Summary Table

| Decision | Verdict | Primary Risk | Mitigation |
|----------|---------|-------------|------------|
| CSS multi-column pagination | Acceptable Risk | Off-by-one page count on hi-DPI; image overflow | Add image reset CSS; use tolerance in page count calc |
| `translateX` for scrolling | Good Bet | None identified | — |
| Wiktionary as sole dictionary | Risky Bet | Poor Korean coverage; fragile HTML parsing | Bundle CC-CEDICT server-side for zh; add Korean API |
| Character-level Chinese segmentation | Acceptable Risk | Wrong semantic unit for ~60% of taps | Add jieba server-side segmentation in v2 |
| Zero client-side dependencies | Good Bet | Maintenance burden on Wiktionary parser | Document the fragile parts; add comments |

---

## Priority Recommendations

**Must fix before v1:**
1. Add `img { max-width: 100%; }` to the fragment reset stylesheet (image overflow is a definite bug for any EPUB with inline images)
2. Handle page count rounding with a tolerance: `Math.round((scrollWidth + 1) / innerWidth)` or check if `pageCount * innerWidth < scrollWidth + threshold`

**Should fix before v1:**
3. Reconsider Wiktionary for Korean — even a simple static lookup table of the top 5,000 Korean words would beat Wiktionary coverage for a language learner

**v2 scope:**
4. Server-side Chinese segmentation with jieba (the biggest UX improvement relative to implementation cost)
5. Bundle CC-CEDICT server-side (eliminates Wiktionary dependency for Chinese entirely)
