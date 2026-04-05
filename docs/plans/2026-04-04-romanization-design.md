# Romanization for Dictionary Lookup

**Date:** 2026-04-04
**Status:** Approved

## Goal

Surface pinyin (Chinese) and Revised Romanization (Korean) in the word-lookup popup, on a separate line below the native-script pronunciation. Supports multiple readings (e.g. heteronyms like 中 → zhōng / zhòng).

## Approach

Library-only, server-side at lookup time. No network calls; romanization is baked into the disk-cached entry.

- **Chinese:** `pypinyin` — tone-mark style, `heteronym=True` to capture all readings per character
- **Korean:** `korean-romanizer` — implements the official Revised Romanization standard

## Backend Changes

### `pyproject.toml`
Add to `[project] dependencies`:
- `pypinyin` (pinned)
- `korean-romanizer` (pinned)

### `_romanize_hangul_simple(text)`
Replace stub with `Romanizer(text).romanize()` from `korean_romanizer`. Wrap in try/except, return `None` on failure.

### `WiktionaryProvider.lookup`
After building `readings`, populate `romanization` per reading using `pypinyin.pinyin(word, style=Style.TONE, heteronym=True)`. Each character returns a list of possible readings; produce one `readings` entry per unique combination. Join multi-character pinyin with spaces.

### `NIKLProvider.lookup`
No structural change — `_romanize_hangul_simple` is already called and stored in `readings[0].romanization`. Un-stubbing the function is sufficient.

### `normalize_dict_response`
No schema change — `readings[n].romanization` already exists and supports `None`.

## Frontend Changes

### `index.html`
Add between `#popup-pronunciation` and `#popup-definitions`:
```html
<div id="popup-romanization"></div>
```

### `app.js`
In `showPopup`, add `romanization` parameter. Build it by collecting all non-null `romanization` values from `readings[]` and joining with ` / `. Set `#popup-romanization` text content.

### `style.css`
Style `#popup-romanization` similarly to `#popup-pronunciation` but muted (e.g. `color: var(--muted)` or similar) to visually distinguish native phonetic from romanization.

## Error Handling

Both library calls are wrapped in try/except. On failure, `romanization` falls back to `None`. Frontend already handles null romanization (field is omitted/empty).

## Testing

- Unit test: `pypinyin` returns tone-marked pinyin for Chinese word
- Unit test: heteronym case (e.g. 中) produces multiple `readings` entries
- Unit test: `_romanize_hangul_simple("안녕")` returns `"annyeong"`
- Update existing `WiktionaryProvider` and `NIKLProvider` mock tests to assert `romanization` field in output
