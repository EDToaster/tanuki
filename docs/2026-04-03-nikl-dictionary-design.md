# NIKL Korean Dictionary Integration Design

**Date:** 2026-04-03  
**Status:** Proposal  
**Author:** explorer-nikl

---

## Summary

The ebook reader currently routes all dictionary lookups from the browser directly to `en.wiktionary.org`. This works for Chinese but is inadequate for Korean: Wiktionary's Korean coverage is lemma-only and requires fragile HTML scraping. The National Institute of Korean Language (국립국어원, NIKL) provides a free, structured API at `krdict.korean.go.kr` with ~50,000 entries.

This document designs:
1. NIKL API integration via a backend proxy
2. A source-agnostic, language-agnostic provider/adapter pattern
3. A normalized dictionary response format for all sources
4. Frontend changes to use the unified endpoint
5. Interaction with the particle-stripping `data-lookup` attribute

---

## 1. NIKL API Details

### Base URL and Endpoints

```
Base: https://krdict.korean.go.kr/api/

Search:  GET /api/search
Detail:  GET /api/view
```

### Authentication

- **Required:** 32-hex-digit API key passed as `key=` query parameter
- **Cost:** Free — register at `https://krdict.korean.go.kr/openApi/openApiRegister`
- **Rate limit:** 50,000 requests per day per key
- **CORS:** **Not supported.** The API requires an API key in the URL, making browser-direct calls both impractical (key exposure) and blocked by CORS policy. Backend proxy is mandatory.

### Search Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `key` | Yes | 32-hex API key |
| `q` | Yes | Search term (UTF-8) |
| `num` | No | Results per page (default 10, max 100) |
| `start` | No | Pagination offset (default 1) |
| `sort` | No | `dict` (alphabetical) or `popular` |
| `translated` | No | `y` to include English translations |
| `trans_lang` | No | `1` = English |

### Response Format

**The API returns XML only** — no JSON option exists.

Example search response for `나무` (tree):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<channel>
  <total>3</total>
  <start>1</start>
  <num>10</num>
  <item>
    <target_code>26655</target_code>
    <word>나무</word>
    <sup_no>0</sup_no>
    <pronunciation>나무</pronunciation>
    <word_grade>초급</word_grade>
    <pos>명사</pos>
    <sense>
      <sense_order>1</sense_order>
      <definition>단단한 줄기에 가지와 잎이 달린 식물.</definition>
      <translation>
        <trans_lang>1</trans_lang>
        <trans_word>tree</trans_word>
        <trans_dfn>A plant with a firm stem, branches, and leaves.</trans_dfn>
      </translation>
    </sense>
    <sense>
      <sense_order>2</sense_order>
      <definition>나무의 줄기나 가지를 통틀어 이르는 말.</definition>
    </sense>
    <link>https://krdict.korean.go.kr/dicSearch/search?mainSearchWord=나무</link>
  </item>
</channel>
```

### Field Mapping

| XML field | Semantic | Notes |
|-----------|----------|-------|
| `<word>` | Headword | Citation form |
| `<pronunciation>` | Pronunciation text | Korean phonetic (same script as word, with tone marks sometimes) |
| `<pos>` | Part of speech | Korean: 명사, 동사, 형용사, 부사, etc. |
| `<word_grade>` | TOPIK level | 초급/중급/고급 (beginner/intermediate/advanced) |
| `<sense>/<definition>` | Korean definition | Native Korean prose |
| `<sense>/<translation>/<trans_word>` | English gloss | Short English equivalent |
| `<sense>/<translation>/<trans_dfn>` | English definition | Full English definition |
| `<link>` | Source URL | Full KRDICT entry URL |

### Known Limitations

- **Romanization absent from search response.** The `/api/search` response has `<pronunciation>` in Hangul only. Romanization (revised romanization or IPA) is available in the `/api/view` endpoint's `pronunciation_info` array, but requires a second lookup by `target_code`.
- **Verbs indexed in citation form (-다).** `먹다`, `가다` — not inflected forms. This complements the particle-stripping strategy: for nouns, josa stripping + NIKL works; for verbs, the word still needs to be a citation form.
- **~50k entries** — comprehensive for everyday vocabulary but not exhaustive for literary or archaic terms. Wiktionary fallback is still valuable.

---

## 2. Integration Strategy: Backend Proxy with Provider Pattern

### Why Backend Proxy Is Mandatory

1. **CORS:** The krdict API does not set `Access-Control-Allow-Origin` headers.
2. **API key security:** Exposing the key in frontend JS leaks it to all users.
3. **XML → JSON transformation:** The backend converts NIKL's XML to normalized JSON, shielding the frontend from format complexity.
4. **Caching:** A per-word cache in the backend prevents repeat lookups from burning the 50k/day limit.

### Provider / Adapter Pattern

The existing design calls Wiktionary directly from the browser. The new design moves all dictionary lookups to a single backend endpoint, with source selection handled server-side. The frontend becomes completely source-agnostic.

**Interface (conceptual — Python):**

```
DictProvider:
  .lookup(word: str) → Optional[NormalizedEntry]
  .name: str
  .languages: list[str]  # ISO 639-1 codes this provider handles
```

**Implementations:**

| Provider | Languages | Source |
|----------|-----------|--------|
| `NIKLProvider` | `["ko"]` | `krdict.korean.go.kr/api/search` |
| `WiktionaryProvider` | `["ko", "zh", ...]` | `en.wiktionary.org/w/api.php` (existing logic, moved server-side) |
| `CCCEDICTProvider` | `["zh"]` | (future v2, local file lookup) |

**Language → provider chain registry:**

```
PROVIDER_CHAINS = {
  "ko": [NIKLProvider, WiktionaryProvider],
  "zh": [WiktionaryProvider],        # v2: [CCCEDICTProvider, WiktionaryProvider]
  "*":  [WiktionaryProvider],        # fallback for any language
}
```

When the backend handles `GET /api/dict?word=학교&lang=ko`, it:
1. Selects the chain for `ko`: `[NIKLProvider, WiktionaryProvider]`
2. Calls `NIKLProvider.lookup("학교")` — returns a result → respond immediately
3. If NIKL returns `None` (no result or error), calls `WiktionaryProvider.lookup("학교")` as fallback
4. If both return `None`, responds with `{ "word": "학교", "not_found": true, "source_url": null }`

### Unified Endpoint

```
GET /api/dict?word={word}&lang={lang}
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `word` | Yes | The lookup term (URL-encoded) |
| `lang` | No | ISO 639-1 language code (default: `"*"`) |

**Response:** Always JSON, normalized format (see §3).

**Frontend change:** Replace the direct Wiktionary `fetch()` call with:

```
fetch(`/api/dict?word=${encodeURIComponent(word)}&lang=${lang}`)
```

where `lang` comes from the `<article data-lang="...">` attribute already present in the rendered chapter.

### Wiktionary Migration

The existing Wiktionary parsing happens client-side (MediaWiki HTML → DOM scraping). Under the new design, this logic moves to the backend `WiktionaryProvider`. The frontend no longer parses any HTML — it only renders the normalized JSON. This is a simplification of the frontend.

---

## 3. Normalized Dictionary Response Format

All providers return the same JSON shape. The frontend renders this shape without knowing the source.

```json
{
  "word": "나무",
  "readings": [
    {
      "text": "나무",
      "romanization": "namu"
    }
  ],
  "definitions": [
    {
      "pos": "noun",
      "text": "A plant with a firm stem, branches, and leaves.",
      "text_ko": "단단한 줄기에 가지와 잎이 달린 식물."
    },
    {
      "pos": "noun",
      "text": "Wood; timber.",
      "text_ko": "나무의 줄기나 가지."
    }
  ],
  "source": "nikl",
  "source_url": "https://krdict.korean.go.kr/dicSearch/search?mainSearchWord=나무",
  "not_found": false
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `word` | string | The looked-up term |
| `readings` | array | Pronunciation entries |
| `readings[].text` | string | Pronunciation in native script (Hangul/pinyin for zh) |
| `readings[].romanization` | string? | Romanization (revised romanization for ko, pinyin for zh) |
| `definitions` | array | Ordered definitions |
| `definitions[].pos` | string? | Part of speech in English ("noun", "verb", "adjective", etc.) |
| `definitions[].text` | string | Definition text in English |
| `definitions[].text_ko` | string? | Korean-language definition (NIKL only; omit for Wiktionary) |
| `source` | string | `"nikl"` \| `"wiktionary"` \| `"cedict"` |
| `source_url` | string? | URL for the full entry page |
| `not_found` | bool | True if all providers returned no result |

### Provider → Normalized Field Mapping

**NIKLProvider:**

| Normalized field | NIKL XML source |
|-----------------|----------------|
| `word` | `<word>` |
| `readings[0].text` | `<pronunciation>` |
| `readings[0].romanization` | Computed via Revised Romanization of Korean algorithm (Python library `korean-romanizer` or stdlib transliteration — see §3.1) |
| `definitions[].pos` | `<pos>` → English translation (명사→noun, 동사→verb, 형용사→adjective, 부사→adverb, etc.) |
| `definitions[].text` | `<translation>/<trans_word>` + `/<trans_dfn>` (English gloss, preferred; concatenated if both present) |
| `definitions[].text_ko` | `<sense>/<definition>` (native Korean) |
| `source_url` | `<link>` |

**WiktionaryProvider:**

| Normalized field | Wiktionary source |
|-----------------|-----------------|
| `word` | Page title |
| `readings[0].text` | Pronunciation section (IPA or native script) |
| `readings[0].romanization` | From `{{ko-IPA}}` or pinyin annotation |
| `definitions[].pos` | Section heading ("Noun", "Verb", etc.) |
| `definitions[].text` | Definition text (stripped of wiki markup) |
| `source_url` | `https://en.wiktionary.org/wiki/{word}` |

### 3.1 Romanization for Korean

NIKL search responses return `<pronunciation>` in Hangul only. Romanization is needed for the popup's pronunciation line ("나무 → namu").

Options:
1. **Second NIKL request to `/api/view`** for romanization from `pronunciation_info` — costs an extra API call per lookup.
2. **Server-side Revised Romanization computation** — deterministic, zero API calls. The Revised Romanization of Korean rules are fully algorithmic for modern Hangul. A compact Python implementation (~100 lines) can convert any Hangul string. No new PyPI dependency needed; the algorithm can be self-contained in the codebase.

**Recommendation:** Option 2. Romanization is fully deterministic for the set of Hangul characters used in pronunciation fields. Avoid the extra round-trip to NIKL.

---

## 4. Backend Caching to Protect the 50k/Day Rate Limit

NIKL allows 50,000 requests/day. A single reader session for a Korean novel might generate hundreds of lookups. An in-memory LRU cache keyed on `(word, lang)` — implemented with Python's `functools.lru_cache` or a simple `dict` — eliminates repeat lookups within the session. A warm `dict` persisting to a JSON file at `/tmp/dict_cache.json` on shutdown would survive server restarts without any database dependency.

**Cache strategy:**
- Layer 1: In-memory `dict` (instant, lives until server restart)
- Layer 2: On-disk JSON file at `/tmp/dict_cache.json` (survives restarts, loaded at startup)
- No expiry needed — dictionary definitions don't change

This keeps NIKL consumption well under the daily limit even for active reading sessions.

---

## 5. Popup Rendering: NIKL → Bottom Sheet Mapping

The existing popup design:

```
┌─────────────────────────────┐
│  字                          │
│  zì  (Mandarin)             │
│  ─────────────────────────  │
│  1. character; letter; word │
│  2. (literary) courtesy name│
│                             │
│     [Open in Wiktionary ↗]  │
└─────────────────────────────┘
```

With normalized response, the same popup template works for all sources:

```
┌─────────────────────────────┐
│  나무                        │
│  namu  (Korean)             │
│  ─────────────────────────  │
│  1. [noun] tree; a plant    │
│     with a firm stem...     │
│  2. [noun] wood; timber     │
│                             │
│     [Open in KRDICT ↗]      │
└─────────────────────────────┘
```

### Required Popup Template Changes

1. **Pronunciation line:** Render `readings[0].romanization` + language label. Currently hardcoded for Chinese/pinyin. Change to use `readings[0].text` as primary (for Korean, this IS the Hangul, so show romanization below or inline). Suggested layout:
   ```
   나무
   namu  ·  noun
   ```

2. **Definition source label:** Change "Open in Wiktionary ↗" to use `source_url` and label based on `source`:
   - `nikl` → "Open in KRDICT ↗"
   - `wiktionary` → "Open in Wiktionary ↗"
   - `cedict` → (future) "Open in CC-CEDICT ↗"
   - `null` → hide the link

3. **Definition list:** No change needed — `definitions[].text` maps directly to the numbered list. Optionally show `definitions[].text_ko` as a secondary line in smaller text for advanced learners.

4. **TOPIK level badge (optional):** NIKL returns `<word_grade>` (초급/중급/고급). A small color-coded badge (green/yellow/red) could be shown next to the word for vocabulary level awareness. This is additive and not required for v1.

### Template remains source-agnostic

The frontend popup template renders from the normalized JSON. It never branches on `source`. The source name is only used for the "Open in X" link label. All other rendering is identical regardless of whether NIKL, Wiktionary, or CC-CEDICT provided the data.

---

## 6. Particle Stripping Interaction (`data-lookup`)

The Korean tokenization analysis (explorer-korean) recommended server-side josa stripping: the preprocessing pipeline stores the stripped noun stem in `data-lookup` on each Korean word span:

```html
<!-- Eojeol "학교에서" → data-lookup="학교" -->
<span class="w" data-lookup="학교">학교에서</span>
```

### How the Frontend Uses `data-lookup`

The tap handler extracts the lookup term:

```
const lookupWord = span.dataset.lookup ?? span.innerText;
```

This same pattern works for all languages:
- **Korean with josa:** `data-lookup` set → use it (stripped noun stem)
- **Korean without josa / Chinese jieba:** `data-lookup` may equal `innerText` or be omitted
- **Other languages:** no `data-lookup` → fall back to `innerText`

The dictionary API call becomes:

```
/api/dict?word={lookupWord}&lang={articleLang}
```

where `articleLang` comes from the chapter's `<article data-lang="ko">` attribute.

**This design composes cleanly:** `data-lookup` is the normalization layer for tokenization irregularities; `/api/dict` is the normalization layer for dictionary source irregularities. Each concern is handled at its own layer.

### Interaction Table

| Eojeol | `data-lookup` | NIKL lookup | Expected result |
|--------|--------------|-------------|----------------|
| `학교에서` | `학교` | `학교` → ✓ | "school; educational institution" |
| `사랑해요` | not set (verb, josa stripping doesn't apply) | `사랑해요` → ✗, fallback `사랑해요` Wiktionary → ✗ | "No result" |
| `먹었어요` | not set | `먹었어요` → ✗ | "No result" — verb inflection not handled in v1 |
| `나무가` | `나무` | `나무` → ✓ | "tree; wood" |

Verb inflections remain unsolved in v1 (confirmed by explorer-korean). The NIKL integration doesn't change this — NIKL is lemma-indexed just like Wiktionary.

---

## 7. Backend Endpoint Specification

```
GET /api/dict?word={word}&lang={lang}

Query params:
  word  (required)  URL-encoded lookup term
  lang  (optional)  ISO 639-1 language code ("ko", "zh", etc.) — default "*"

Response (200 OK):
  Content-Type: application/json
  Body: NormalizedEntry JSON (see §3)

Response (400):
  { "error": "missing word parameter" }

Response (500):
  { "error": "upstream lookup failed", "word": "...", "not_found": true }
```

**Provider resolution logic:**

```
1. Look up PROVIDER_CHAINS[lang] (fall back to PROVIDER_CHAINS["*"])
2. For each provider in chain:
   a. Check in-memory cache → return cached entry if hit
   b. Call provider.lookup(word)
   c. On success: cache result, return normalized JSON
   d. On None/error: try next provider
3. If all providers fail: return { "not_found": true, "word": word, ... }
```

**Error handling:** Individual provider failures (network error, upstream 5xx) are caught and logged. They do not propagate to the frontend — the next provider in the chain is tried instead.

---

## 8. Security Considerations

- **API key:** Stored as environment variable `KRDICT_API_KEY`, never in code or committed to git. Passed to the Docker container via `environment:` in compose.
- **Input validation:** The `word` parameter is URL-decoded and passed to the NIKL API as a query parameter (not interpolated into SQL or shell). No injection risk beyond what Python's `urllib` handles.
- **Response sanitization:** NIKL XML is parsed with `xml.etree.ElementTree`. Before inserting any field into the normalized JSON response, strip any HTML tags from definition text (NIKL definitions are plain text, but defensive stripping costs nothing).
- **Proxy response size:** Cap the number of definitions at 10 and senses at 5 to prevent unexpectedly large responses from NIKL passing through to the frontend.

---

## 9. Environment Variable Configuration

New required configuration:

```yaml
# docker-compose.yml addition to ebook-reader service
environment:
  - KRDICT_API_KEY=${KRDICT_API_KEY}
```

The backend reads this at startup:

```python
import os
KRDICT_API_KEY = os.environ.get("KRDICT_API_KEY")  # None if not set
```

If `KRDICT_API_KEY` is `None`, `NIKLProvider` is disabled and omitted from the provider chain. The system degrades gracefully to Wiktionary-only mode — useful during development without a key.

---

## 10. Changes to the Design Document

The following additions should be incorporated into `2026-04-03-ebook-reader-design.md`:

### New Backend Endpoint

Add to the endpoints table:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dict` | Dictionary lookup — proxies NIKL, Wiktionary, etc. |

### Updated Architecture Diagram

```
┌─────────────┐     GET /library                ┌──────────────────┐
│             │ ──────────────────────────────▶  │                  │
│  Browser    │     GET /book/:id/chapter/:n      │  Python server   │
│  (pure JS)  │ ──────────────────────────────▶  │  (media-stack)   │
│             │     GET /api/dict?word=X&lang=ko  │  /books on disk  │
│             │ ──────────────────────────────▶  │                  │
│             │           (no direct 3rd-party    │  ──────────────▶ NIKL API
│             │            calls from browser)    │  ──────────────▶ Wiktionary
└─────────────┘                                   └──────────────────┘
```

All dictionary traffic now flows through the backend. The frontend makes no direct third-party API calls.

### Updated Dictionary Popup Section

Replace the Wiktionary-specific fetch code with the unified `/api/dict` call. The popup rendering logic is unchanged except for the dynamic "Open in X" link.

### New Configuration Section

Document the `KRDICT_API_KEY` environment variable and the graceful degradation behavior.

---

## 11. Implementation Order

Recommended sequence (not writing code — describing dependency order for the implementation plan):

1. Define `NormalizedEntry` dataclass / TypedDict
2. Implement `DictProvider` base class / protocol
3. Implement `NIKLProvider` (XML parse → normalized)
4. Implement Korean romanization utility (Revised Romanization)
5. Implement `WiktionaryProvider` (port existing frontend parsing to Python)
6. Implement provider chain registry + `/api/dict` endpoint
7. Implement in-memory + on-disk cache
8. Update frontend: replace Wiktionary `fetch()` with `/api/dict` call
9. Update frontend: render `source_url` as dynamic link label

Steps 3–5 are independent and can be done in parallel.

---

## Appendix: POS Code Mapping (Korean → English)

| Korean `<pos>` | English |
|----------------|---------|
| 명사 | noun |
| 동사 | verb |
| 형용사 | adjective |
| 부사 | adverb |
| 대명사 | pronoun |
| 수사 | numeral |
| 관형사 | determiner |
| 감탄사 | interjection |
| 조사 | particle |
| 의존명사 | bound noun |
| 보조동사 | auxiliary verb |
| 보조형용사 | auxiliary adjective |

---

## Appendix: NIKL Error Codes

| Code | Meaning | Handling |
|------|---------|---------|
| `010` | Daily request limit exceeded | Log warning, skip NIKL, use fallback |
| `020` | Unregistered key | Log error, disable NIKLProvider for session |
| `021` | Key temporarily unavailable | Treat as transient, use fallback |
| `100`–`216` | Parameter validation errors | Log error, return not_found |
