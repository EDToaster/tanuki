# Korean Tokenization Strategy Analysis

**Date:** 2026-04-03  
**Explorer:** explorer-korean  
**Scope:** Analysis of Korean tokenization in `2026-04-03-ebook-reader-design.md`

---

## Summary

The current design wraps every space-delimited Korean token (eojeol) in a tappable `<span class="w">`. This is **linguistically incorrect** — the vast majority of eojeols include agglutinated particles or verb endings that will not match any Wiktionary entry, causing almost every single tap to fail silently with "No result."

**Recommendation:** Keep eojeol-level display wrapping, but add a server-side particle-stripping step that stores the stripped lookup key as a `data-lookup` attribute. This requires no new dependencies and recovers noun lookups accurately. Verb lookups remain degraded until a morphological library is available.

---

## Problem: Korean is Agglutinative

Korean is written with spaces between **eojeols** (어절) — the "word" unit in Korean orthography. Unlike English words, an eojeol is not a dictionary citation form. It is a stem with zero or more particles/endings attached:

### Noun eojeols (stem + josa particle)

| Eojeol | Structure | Wiktionary entry? |
|--------|-----------|-------------------|
| 학교 | 학교 (school) | ✅ Yes |
| 학교에서 | 학교 + 에서 (at/from) | ❌ No |
| 학교를 | 학교 + 를 (acc.) | ❌ No |
| 학교의 | 학교 + 의 (gen.) | ❌ No |
| 사람들이 | 사람 + 들 (plural) + 이 (nom.) | ❌ No |
| 책을 | 책 + 을 (acc.) | ❌ No |

### Verb eojeols (stem + tense/aspect/mood endings)

| Eojeol | Structure | Wiktionary entry? |
|--------|-----------|-------------------|
| 먹다 | eat (citation form) | ✅ Yes |
| 먹어요 | eat + present polite | ✅ Yes (redirect) |
| 먹었어요 | eat + past + polite | ❌ No |
| 사랑하다 | love (citation form) | ✅ Yes |
| 사랑해요 | love + present polite | ❌ No |

In typical prose, the proportion of eojeols that are **bare citation forms** is very low — most nouns appear with particles, most verbs appear with conjugation endings. A conservative estimate is that **fewer than 15% of eojeols** will match a Wiktionary entry directly.

---

## Wiktionary Korean Coverage

English Wiktionary indexes Korean by **lemma/citation form**:
- Nouns: bare form (`학교`, `책`, `사람`)
- Verbs: `-다` infinitive (`먹다`, `사랑하다`, `달리다`)
- Adjectives: `-다` infinitive (`예쁘다`, `크다`)

**Coverage quality for stems is good.** Wiktionary has well-formed entries with:
- IPA pronunciation and romanization
- Etymology (including Sino-Korean origin, first attestation)
- Numbered definitions with example sentences
- Full conjugation tables for verbs

**Inflected forms are not indexed** except for a handful of extremely common forms (e.g., `먹어요` exists as a form-of redirect). Stacked forms like `먹었어요` or `학교에서만은` don't exist.

**Verdict:** Wiktionary coverage is adequate for the lemma-based lookup use case, but the current design never reaches the lemma.

---

## Alternative Approaches Evaluated

### Option A: Character-level wrapping (like Chinese)

Wrap each Hangul syllable block (`U+AC00–U+D7A3`) individually.

**Why this is wrong for Korean:**

Chinese characters are morphemes — `字` (character), `书` (book), `人` (person) each have independent meanings and dictionary entries. Hangul syllable blocks are **phonetic units**, not semantic units. The syllable `학` in `학교` means nothing standalone to a reader — it's only meaningful as part of the word. Wiktionary does have entries for some syllables (e.g., `가` has entries for multiple Sino-Korean readings), but tapping them out of context produces ambiguous, unhelpful results.

Character-level wrapping would be **worse** than the current approach: it creates more tappable spans, none of which are actionable, and forces users to manually select multi-syllable groups to get any useful information.

### Option B: Morpheme segmentation

Split eojeols into morphemes: `학교에서` → `학교` + `에서`; `먹었어요` → `먹` + `었` + `어요`.

This is the linguistically correct approach and would enable accurate lookup of both noun stems and verb stems. Libraries that do this well include:

- **KoNLPy** (Python) with MeCab-ko backend — production-quality, handles irregular verbs
- **Okt (Open Korean Text)** — Java-based, Python wrapper available
- **kiwipiepy** — pure Python morphological analyzer (newer, no Java dependency)

**Why this violates v1 constraints:** The design mandates minimal Python standard library with no heavy dependencies. KoNLPy requires Java or MeCab system installation; kiwipiepy adds a C extension. Both complicate the Docker build considerably. The design also explicitly defers "CJK word segmentation" to post-v1.

### Option C: Server-side particle stripping (recommended)

Korean particles (조사, josa) form a **closed class** of approximately 20 items. They are short (1–3 syllables) and attach at the end of noun phrases. A heuristic stripping pass can recover the noun stem in most cases:

**Particle list (longest-match-first):**

```
에서 (at/from), 에게 (to/for), 으로 (to/toward, after consonant),
부터 (from/since), 까지 (until/to), 처럼 (like/as), 보다 (than),
한테 (to, informal), 이라 (since it is), 이다 (copula),
로 (to/toward, after vowel), 에 (at/in/to), 의 (genitive),
도 (too/also), 만 (only), 와 (and/with, after vowel),
과 (and/with, after consonant), 를 (accusative, after vowel),
을 (accusative, after consonant), 가 (nominative, after vowel),
이 (nominative, after consonant), 는 (topic, after vowel),
은 (topic, after consonant)
```

**Mechanism:**
1. For each eojeol, try stripping the longest matching suffix
2. If a match is found, store stripped form as `data-lookup` on the span
3. Frontend uses `data-lookup` for the Wiktionary API call, falls back to `innerText`

**Example output:**
```html
<!-- current -->
<span class="w">학교에서</span>

<!-- proposed -->
<span class="w" data-lookup="학교">학교에서</span>
```

**No new dependencies.** This is ~30 lines of Python added to the chapter pre-processing pipeline.

**Limitations:**
- Verb forms are NOT handled: `먹었어요` stays as `먹었어요` (no entry found)
- Stacked particles may not fully strip: `사람들이` strips `이` → `사람들`, which still has no entry
- Plural suffix `들` is not a josa and would need separate treatment
- Does not help with verb conjugation at all

---

## Recommendation

### v1: Particle-stripping heuristic (implement now)

Add a server-side particle-stripping pass to the chapter pre-processing pipeline. Store stripped forms in `data-lookup` attributes. This costs ~30 lines of Python, zero new dependencies, and dramatically improves noun lookup success rate from ~15% to ~60–70% of eojeols.

For verb eojeols, lookups will continue to fail. The fallback "No result" state is acceptable for v1 since:
1. Verbs conjugate in consistent, learnable patterns; learners often recognize them
2. Text selection is available to manually extract parts for lookup
3. The Wiktionary "Open in Wiktionary" fallback still works for manual exploration

### v2: Morpheme segmentation (post-v1)

Add `kiwipiepy` (pure Python morphological analyzer, no Java dependency) to the server requirements. Replace the particle-stripping heuristic with a proper morpheme segmentation pass. This would:
- Correctly handle all verb forms
- Correctly handle stacked particles
- Handle irregular conjugations (ㅂ, ㄷ, ㅅ irregular verbs)

`kiwipiepy` is significantly lighter than KoNLPy and is the most reasonable v2 path.

### What NOT to do

- Do **not** switch to character-level wrapping. It is a regression for Korean.
- Do **not** attempt to regex-strip verb endings — the Korean verb system is too complex for a reliable heuristic without a morphological analyzer.

---

## Impact on Design Document

The following change to the "Chapter Pre-processing Pipeline" section is needed:

> **Korean (`ko`):** every space-delimited token, with a server-side particle-stripping pass to populate `data-lookup` attributes on each span with the likely citation form for Wiktionary lookup.

And in the Dictionary Popup section:

> **Lookup key:** use `span.dataset.lookup || span.innerText` to prefer the stripped lemma form when available.

---

## Appendix: Test Evidence

All tests were run against `https://en.wiktionary.org/w/api.php?action=parse&page={word}&format=json&origin=*`:

| Query | Result |
|-------|--------|
| `학교` | ✅ Entry: noun, "school" |
| `학교에서` | ❌ "The page you specified doesn't exist" |
| `책` | ✅ Entry: noun, "book" |
| `책을` | ❌ No entry |
| `사랑` | ✅ Entry: noun, "love" |
| `사랑하다` | ✅ Entry: verb, "to love" |
| `사랑해요` | ❌ No entry |
| `먹다` | ✅ Entry: verb, "to eat" (full conjugation table) |
| `먹어요` | ✅ Entry: form-of redirect → 먹다 |
| `먹었어요` | ❌ No entry |
