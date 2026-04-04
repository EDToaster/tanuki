# EPUB Styling Normalization & Reader CSS Design

**Date:** 2026-04-03  
**Status:** Proposal  
**Author:** explorer-styling  

## Overview

EPUBs are styled artifacts. Each publisher ships their own CSS: `font-family: "Times New Roman"`, `font-size: 11px`, inline `style="color:#333; margin-left:2em"`, `<font>` tags from legacy toolchains, and `<br><br><br>` for paragraph spacing. Left unfiltered, the reader displays a patchwork of publisher aesthetics instead of a unified reading experience.

This document designs two things:

1. **Server-side normalization pipeline additions** — what gets stripped, rewritten, or collapsed during chapter pre-processing before HTML is sent to the client.
2. **Reader CSS** — the unified stylesheet the reader applies to all books.

---

## 1. Server-Side Normalization Pipeline

### 1.1 Current Pipeline (from design doc)

The existing pipeline does:
1. Open EPUB ZIP, locate chapter via OPF spine
2. Extract chapter HTML
3. Strip `<head>`, EPUB-internal hrefs, external stylesheets
4. Wrap tappable units in `<span class="w">`
5. Return `<article data-lang="zh">` fragment

**Gap:** EPUB content can still carry inline `style` attributes, `<font>` tags, `align` attributes, dimension attributes, and (critically) `<script>` tags and `on*` event handlers.

### 1.2 Proposed Normalization Steps (insert after step 3, before step 4)

#### Step 3a — Security sanitization (highest priority)

Strip all constructs that can execute code:

| Target | Action |
|--------|--------|
| `<script>` elements | Strip element and contents entirely |
| `<style>` elements | Strip element and contents entirely |
| `on*` event handler attributes (`onclick`, `onerror`, `onload`, etc.) | Strip attribute |
| `href="javascript:..."` | Strip href entirely (set to `#` or remove anchor) |
| `src="javascript:..."` | Strip attribute |
| `<iframe>`, `<object>`, `<embed>` | Strip element and contents |
| `<form>`, `<input>`, `<button>` | Strip element (unwrap to text content where applicable) |
| `data:` URIs on `<img src>` | Keep (base64 images in EPUBs are common) |
| `data:` URIs on `<a href>` | Strip |

**Rationale:** Chapter HTML is injected via `innerHTML` in the frontend. A malicious or corrupted EPUB could execute arbitrary JS in the reader page context (disc-4064a430).

#### Step 3b — Inline style normalization (allowlist approach)

**Recommendation: Allowlist a small set of semantic CSS properties; strip everything else.**

Stripping all inline styles is tempting for simplicity, but loses meaningful semantic styling that EPUB authors use deliberately:

- `font-style: italic` — marks foreign words, titles, technical terms
- `font-weight: bold` — emphasis (though `<strong>` is preferred)
- `font-style: normal` — cancels inherited italic (sometimes used inside italicized blocks)

Strip vs. keep analysis:

| CSS Property | Decision | Reason |
|---|---|---|
| `font-style: italic` / `normal` | **Keep** | Semantic — marks foreign words, emphasis |
| `font-weight: bold` / `normal` | **Keep** | Semantic — emphasis |
| `color` | **Strip** | Publisher branding, conflicts with dark mode |
| `background-color` | **Strip** | Same |
| `font-family` | **Strip** | Core normalization goal |
| `font-size` | **Strip** | Reader controls size |
| `line-height` | **Strip** | Reader CSS handles this |
| `margin`, `padding` | **Strip** | Reader CSS handles layout |
| `text-align` | **Strip** (see 3d) | Use structural approach instead |
| `width`, `height` | **Strip** | See image handling (3e) |
| `writing-mode` | **Strip** (v1) | Not supported; forward-compat handled separately (§5) |
| `display` | **Strip** | Reader CSS controls display |
| `position`, `float`, `z-index` | **Strip** | Breaks column layout |
| `border`, `outline` | **Strip** | Publisher decoration |
| `text-decoration` | **Conditional** | Keep `underline` only if on `<a>` (semantic); strip on other elements |
| `vertical-align: sub/super` | **Keep** | Needed if `<sub>`/`<sup>` not used; marks footnotes |

**Implementation pattern:**

```
allowlisted_inline_properties = {
    "font-style",
    "font-weight",
    "vertical-align",  # only sub/super values
}

For each element with style="...":
    parse CSS declarations
    keep only those in allowlist
    for vertical-align: only keep if value is "sub" or "super"
    if no properties remain: remove style attribute
    else: rewrite style attribute with surviving properties only
```

#### Step 3c — `class` attribute handling

**Decision: Strip all class attributes.**

EPUB class attributes reference the EPUB's own stylesheets, which are already stripped. Keeping them is meaningless noise. Some EPUBs use structural class names like `class="chapter-title"` or `class="dropcap"`, but since we have no corresponding CSS, these are inert — they add bytes with no benefit and could collide with reader CSS class names.

Exception: **Do not strip** `class="w"` added by our own pipeline in step 4.

Implementation: strip `class` attribute during sanitization (step 3a/3b), then add `class="w"` in step 4.

#### Step 3d — `align` and `valign` attribute handling

Legacy HTML attributes `align` (on `<p>`, `<div>`, `<td>`, `<th>`, `<img>`) and `valign` (on `<td>`, `<th>`) are presentational. Strip both. The reader CSS handles default text alignment; centered images are handled via CSS on the figure/img element (see §3).

| Attribute | Elements | Action |
|---|---|---|
| `align` | `<p>`, `<div>`, `<h1>`–`<h6>`, `<blockquote>` | Strip |
| `align` | `<img>` | Strip (use CSS centering) |
| `align` | `<table>`, `<td>`, `<th>` | Strip |
| `valign` | `<td>`, `<th>`, `<tr>` | Strip |
| `hspace`, `vspace` | `<img>`, `<table>` | Strip |

#### Step 3e — `<font>` tag handling

`<font>` is an obsolete HTML element carrying `face`, `size`, and `color` attributes. It should be **unwrapped**: replace `<font ...>content</font>` with just `content`, preserving child nodes in place. Do not attempt to convert font attributes to inline styles — everything in them is publisher styling that conflicts with normalization goals.

#### Step 3f — `<br>` abuse handling

EPUBs from older conversion tools use repeated `<br>` as paragraph spacing. Decision matrix:

| Pattern | Action | Reason |
|---|---|---|
| Single `<br>` | **Keep** | Semantic line break (e.g., poetry, addresses) |
| 2 consecutive `<br>` | **Collapse to paragraph break** | Almost always used for spacing, not semantic |
| 3+ consecutive `<br>` | **Collapse to paragraph break** | Definitely spacing abuse |

"Paragraph break" means: wrap the content before the run in `</p><p>` (or insert a `<p>` boundary at that point). This integrates correctly with CSS `margin-top` on `p` elements.

**Edge case:** `<br>` inside `<pre>` — leave entirely alone. `<br>` inside a list item — apply the same 2+ rule.

#### Step 3g — Image dimension attributes

Strip `width` and `height` attributes from `<img>` elements. The reader CSS applies:
```css
img { max-width: 100%; height: auto; }
```

This prevents images wider than the column from overflowing into adjacent columns (disc-1536ffd3).

Exception: **Keep `width` and `height` on `<table>` cells** as percentage values only (e.g., `width="50%"`) since these can be used for table layout. Strip pixel values.

---

## 2. HTML Tag and Attribute Allowlist

### 2.1 Philosophy

Use an **allowlist** (not blocklist). Everything not on the list is stripped. Elements with only disallowed children become their text content (unwrapped). Block-level unknown elements are replaced with `<div>`, inline unknown elements with `<span>`.

This is strictly safer and more predictable than trying to enumerate what to remove.

### 2.2 Allowed Elements

#### Structural / Block

| Element | Keep | Notes |
|---|---|---|
| `article` | Yes | Top-level wrapper (added by pipeline) |
| `section` | Yes | Chapter subdivisions |
| `div` | Yes | Generic block (ubiquitous in EPUBs) |
| `p` | Yes | Paragraphs — primary text container |
| `h1`–`h6` | Yes | Headings |
| `blockquote` | Yes | Quotations |
| `pre` | Yes | Preformatted text (code samples, ASCII art) |
| `hr` | Yes | Thematic break |
| `figure` | Yes | Image + caption container |
| `figcaption` | Yes | Image caption |
| `header`, `footer` | Yes | Chapter header/footer sections |
| `nav` | No | EPUB navigation — not needed in fragment |
| `aside` | Yes | Sidebars, notes |
| `address` | Yes | Contact/attribution blocks |

#### Text-level / Inline

| Element | Keep | Notes |
|---|---|---|
| `span` | Yes | Generic inline (used for `.w` wrapping) |
| `em` | Yes | Emphasis (semantic italic) |
| `strong` | Yes | Strong emphasis (semantic bold) |
| `i` | Yes | Italic (alternate voice, foreign words) |
| `b` | Yes | Bold (attention, keywords) |
| `u` | Yes | Underline (proper names in Chinese) |
| `s`, `del` | Yes | Strikethrough |
| `ins` | Yes | Inserted text |
| `mark` | Yes | Highlighted text |
| `small` | Yes | Fine print |
| `sub` | Yes | Subscript |
| `sup` | Yes | Superscript (footnote markers) |
| `abbr` | Yes | Abbreviations |
| `cite` | Yes | Titles |
| `code` | Yes | Inline code |
| `kbd` | Yes | Keyboard input |
| `var` | Yes | Variable |
| `samp` | Yes | Sample output |
| `q` | Yes | Inline quotation |
| `br` | Yes | Line break (after abuse normalization) |
| `wbr` | Yes | Optional line break hint |

#### Links and Media

| Element | Keep | Notes |
|---|---|---|
| `a` | Yes | `href` only if non-javascript, non-external; or strip href |
| `img` | Yes | After dimension stripping |
| `picture` | Yes | Responsive images |
| `source` | Yes | Inside `<picture>` only |

#### Lists

| Element | Keep | Notes |
|---|---|---|
| `ul` | Yes | Unordered list |
| `ol` | Yes | Ordered list |
| `li` | Yes | List item |
| `dl` | Yes | Description list |
| `dt` | Yes | Description term |
| `dd` | Yes | Description details |

#### Tables

| Element | Keep | Notes |
|---|---|---|
| `table` | Yes | Some EPUBs use tables for data |
| `thead`, `tbody`, `tfoot` | Yes | |
| `tr` | Yes | |
| `th`, `td` | Yes | |
| `caption` | Yes | |
| `colgroup`, `col` | Yes | |

#### Ruby / CJK Pronunciation

| Element | Keep | Notes |
|---|---|---|
| `ruby` | Yes | Ruby container (essential for CJK) |
| `rb` | Yes | Ruby base (Obsolete HTML but common in EPUBs) |
| `rt` | Yes | Ruby text (furigana/pinyin) |
| `rp` | Yes | Ruby parenthesis fallback |
| `rtc` | Yes | Ruby text container (for complex ruby) |

#### Explicitly Excluded

| Element | Action |
|---|---|
| `script`, `style`, `link`, `meta`, `head` | Strip with contents |
| `iframe`, `object`, `embed`, `applet` | Strip with contents |
| `form`, `input`, `button`, `select`, `textarea` | Strip |
| `svg` | Strip in v1 (EPUB illustrations via SVG need separate handling) |
| `math` | Strip in v1 |
| `canvas`, `video`, `audio` | Strip |
| `template`, `slot`, `shadow-root` | Strip |
| `font` | Unwrap (3e) |

### 2.3 Allowed Attributes

#### Global (all elements)

| Attribute | Keep | Notes |
|---|---|---|
| `id` | Yes | Fragment anchors (internal links) |
| `lang` | Yes | Language tagging (CJK mixed-language books) |
| `dir` | Yes | Bidi text direction |
| `title` | Yes | Tooltip text |
| `class` | **No** | Strip incoming; pipeline adds `class="w"` separately |
| `style` | **Allowlist** | See §1.2 — only semantic properties |
| `on*` | **No** | Strip all event handlers |
| `data-*` | **Pipeline-only** | Strip incoming `data-*`; pipeline adds `data-lookup` for Korean |

#### Per-element

| Element | Attribute | Keep |
|---|---|---|
| `a` | `href` | Yes (after security check) |
| `a` | `target` | No |
| `img` | `src` | Yes |
| `img` | `alt` | Yes |
| `img` | `width`, `height` | **No** (§1.2g) |
| `img` | `loading` | Yes (`lazy` is fine) |
| `source` | `srcset`, `type`, `media` | Yes |
| `ol` | `start`, `type`, `reversed` | Yes |
| `td`, `th` | `colspan`, `rowspan` | Yes |
| `td`, `th` | `width`, `height` | No |
| `ruby` | — | No special attributes needed |

---

## 3. Reader CSS

The reader stylesheet is the single source of truth for all visual presentation. It is served as `/static/reader.css` and applied to every chapter fragment.

### 3.1 Design Principles

1. **System fonts, not web fonts** — zero extra requests, fast render, respects user's OS preferences
2. **Serif body text** — matches physical book reading conventions; better for long-form reading
3. **Generous line height** — reduces eye fatigue; especially important for CJK text
4. **CJK-first fallback chain** — the primary use case is Chinese/Korean; Latin serif is secondary
5. **Dark mode via `prefers-color-scheme`** — no JS required, OS-level control
6. **No layout assumptions** — the `.chapter-content` multi-column container handles layout; this CSS styles content inside it

### 3.2 Full Reader Stylesheet

```css
/* ===================================================================
   EPUB Reader Stylesheet
   Applied to: .chapter-content > article[data-lang]
   =================================================================== */

/* ------------------------------------------------------------------
   CSS Custom Properties (tokens)
   ------------------------------------------------------------------
   Light mode defaults; dark mode overrides below.
   ------------------------------------------------------------------ */
:root {
  --reader-bg:          #faf9f7;   /* warm off-white, easier than pure white */
  --reader-text:        #1a1a1a;
  --reader-text-muted:  #555;
  --reader-link:        #2c5f8a;
  --reader-border:      #e0ddd8;
  --reader-blockquote:  #f0ede8;
  --reader-mark:        #fff3cd;
  --reader-code-bg:     #f4f1ec;

  /* Typography scale */
  --reader-font-size:    1.1rem;
  --reader-line-height:  1.8;
  --reader-para-gap:     0.9em;

  /* Touch feedback */
  --tap-highlight:      rgba(0, 80, 200, 0.12);
}

/* ------------------------------------------------------------------
   Dark Mode
   ------------------------------------------------------------------ */
@media (prefers-color-scheme: dark) {
  :root {
    --reader-bg:          #1c1c1e;  /* iOS system dark background */
    --reader-text:        #e8e6e3;
    --reader-text-muted:  #999;
    --reader-link:        #6aadde;
    --reader-border:      #38383a;
    --reader-blockquote:  #2c2c2e;
    --reader-mark:        #3d3510;
    --reader-code-bg:     #2c2c2e;
  }
}

/* ------------------------------------------------------------------
   Article container
   ------------------------------------------------------------------ */
article[data-lang] {
  /*
   * System serif stack — prioritizes CJK fonts first since primary
   * content is Chinese/Korean. Latin serif falls through to Georgia.
   *
   * Chinese: Hiragino Mincho (macOS/iOS), Noto Serif CJK SC (Android/Linux),
   *          SimSun fallback (Windows)
   * Japanese: Hiragino Mincho ProN (macOS/iOS), Yu Mincho (Windows)
   * Korean:   AppleMyungjo (macOS/iOS), Noto Serif CJK KR (Android),
   *           Batang (Windows)
   * Latin:    Georgia (universal), then generic serif
   */
  font-family:
    "Hiragino Mincho ProN",
    "Hiragino Mincho Pro",
    "Yu Mincho", "YuMincho",
    "Noto Serif CJK SC",
    "Noto Serif CJK JP",
    "Noto Serif CJK KR",
    "Songti SC", "STSong",
    "SimSun", "NSimSun",
    "AppleMyungjo",
    "Batang", "BatangChe",
    Georgia,
    serif;

  font-size: var(--reader-font-size);
  line-height: var(--reader-line-height);
  color: var(--reader-text);
  background-color: var(--reader-bg);

  /* Improve rendering */
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;

  /*
   * Default text settings — overridden per-lang below.
   * Keep word-break out of here so Latin text hyphenates naturally.
   */
}

/* ------------------------------------------------------------------
   CJK language-specific rules
   ------------------------------------------------------------------ */

/* Chinese (Simplified and Traditional) */
article[data-lang="zh"],
article[data-lang="zh-hans"],
article[data-lang="zh-hant"],
article[data-lang="zh-TW"],
article[data-lang="zh-HK"] {
  /*
   * line-break: strict — enforces strict CJK line-breaking rules
   * (never break before small kana, currency symbols, etc.)
   */
  line-break: strict;

  /*
   * text-justify: inter-character — distributes whitespace between
   * characters for justified text in Chinese typography.
   * Not all browsers support this on non-table contexts, but it
   * degrades gracefully to normal justification.
   */
  text-justify: inter-character;

  /*
   * word-break: keep-all would be wrong for Chinese (no word spaces).
   * Use normal — Chinese line breaking is determined by line-break above.
   */
  word-break: normal;

  /* CJK punctuation compression via font features */
  font-variant-east-asian: proportional-width;
}

/* Japanese */
article[data-lang="ja"] {
  line-break: strict;
  text-justify: inter-character;
  word-break: normal;
  font-variant-east-asian: proportional-width;
}

/* Korean */
article[data-lang="ko"] {
  /*
   * word-break: keep-all — Korean uses spaces between words (eojeols).
   * keep-all prevents breaking within eojeols at line ends, which would
   * produce awkward mid-word wraps. This is the standard approach for
   * Korean body text.
   */
  word-break: keep-all;

  /* Korean does not use inter-character justification */
  text-justify: auto;
  line-break: strict;
}

/* ------------------------------------------------------------------
   Paragraphs
   ------------------------------------------------------------------ */
article[data-lang] p {
  margin-top: 0;
  margin-bottom: var(--reader-para-gap);
  /* No text-indent — modern reading apps avoid it; para gap is sufficient */
}

/* Remove margin from last paragraph in a block to avoid double spacing */
article[data-lang] blockquote > p:last-child,
article[data-lang] li > p:last-child {
  margin-bottom: 0;
}

/* ------------------------------------------------------------------
   Headings
   ------------------------------------------------------------------ */
article[data-lang] h1,
article[data-lang] h2,
article[data-lang] h3,
article[data-lang] h4,
article[data-lang] h5,
article[data-lang] h6 {
  font-weight: 700;
  line-height: 1.3;
  margin-top: 1.6em;
  margin-bottom: 0.4em;
  color: var(--reader-text);

  /*
   * Prevent headings from being orphaned at column breaks.
   * The heading must appear on the same column as at least 2 lines
   * of following text.
   */
  break-after: avoid;
  page-break-after: avoid; /* legacy */
}

article[data-lang] h1 { font-size: 1.6em; margin-top: 0.5em; }
article[data-lang] h2 { font-size: 1.35em; }
article[data-lang] h3 { font-size: 1.15em; }
article[data-lang] h4 { font-size: 1.05em; }
article[data-lang] h5 { font-size: 1em;    font-style: italic; }
article[data-lang] h6 { font-size: 0.9em;  color: var(--reader-text-muted); }

/* ------------------------------------------------------------------
   Blockquote
   ------------------------------------------------------------------ */
article[data-lang] blockquote {
  margin: 1em 0;
  padding: 0.75em 1.25em;
  background: var(--reader-blockquote);
  border-left: 3px solid var(--reader-border);
  border-radius: 0 4px 4px 0;
  color: var(--reader-text-muted);
  font-style: italic;
}

/* ------------------------------------------------------------------
   Lists
   ------------------------------------------------------------------ */
article[data-lang] ul,
article[data-lang] ol {
  margin: 0.5em 0 0.9em 0;
  padding-left: 1.5em;
}

article[data-lang] li {
  margin-bottom: 0.25em;
  line-height: var(--reader-line-height);
}

article[data-lang] dl { margin: 0.5em 0; }
article[data-lang] dt { font-weight: 700; margin-top: 0.5em; }
article[data-lang] dd { margin-left: 1.5em; margin-bottom: 0.25em; }

/* ------------------------------------------------------------------
   Images
   ------------------------------------------------------------------ */
article[data-lang] img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;  /* center within column */

  /*
   * Prevent images from breaking across columns.
   * break-inside: avoid tells the browser not to split an image
   * between two columns. Critical for the CSS multi-column layout.
   */
  break-inside: avoid;
  page-break-inside: avoid; /* legacy */
}

article[data-lang] figure {
  margin: 1em 0;
  break-inside: avoid;
  page-break-inside: avoid;
}

article[data-lang] figcaption {
  font-size: 0.85em;
  color: var(--reader-text-muted);
  text-align: center;
  margin-top: 0.4em;
  font-style: italic;
}

/* ------------------------------------------------------------------
   Horizontal Rule
   ------------------------------------------------------------------ */
article[data-lang] hr {
  border: none;
  border-top: 1px solid var(--reader-border);
  margin: 1.5em auto;
  width: 60%;
}

/* ------------------------------------------------------------------
   Links
   ------------------------------------------------------------------ */
article[data-lang] a {
  color: var(--reader-link);
  text-decoration: underline;
  text-underline-offset: 0.15em;
}

/* ------------------------------------------------------------------
   Preformatted / Code
   ------------------------------------------------------------------ */
article[data-lang] pre {
  background: var(--reader-code-bg);
  border: 1px solid var(--reader-border);
  border-radius: 4px;
  padding: 0.75em 1em;
  overflow-x: auto;
  font-size: 0.85em;
  line-height: 1.5;
  white-space: pre;
  break-inside: avoid;
  page-break-inside: avoid;
}

article[data-lang] code {
  background: var(--reader-code-bg);
  border-radius: 3px;
  padding: 0.1em 0.3em;
  font-size: 0.88em;
  font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
}

article[data-lang] pre code {
  background: none;
  padding: 0;
  font-size: inherit;
}

/* ------------------------------------------------------------------
   Tables
   ------------------------------------------------------------------ */
article[data-lang] table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 0.9em;
  break-inside: avoid;
  page-break-inside: avoid;
}

article[data-lang] th,
article[data-lang] td {
  border: 1px solid var(--reader-border);
  padding: 0.4em 0.6em;
  text-align: left;
  vertical-align: top;
}

article[data-lang] th {
  background: var(--reader-blockquote);
  font-weight: 700;
}

/* ------------------------------------------------------------------
   Ruby / Furigana (see §4 for full notes)
   ------------------------------------------------------------------ */
article[data-lang] ruby {
  ruby-align: center;
}

article[data-lang] rt {
  font-size: 0.5em;        /* rt is typically 50% of base text size */
  font-family:
    "Hiragino Kaku Gothic ProN",  /* sans-serif for better rt readability */
    "Hiragino Sans",
    "Noto Sans CJK JP",
    "Noto Sans CJK SC",
    "Yu Gothic",
    "Meiryo",
    sans-serif;
  color: var(--reader-text-muted);
  font-style: normal;       /* cancel any inherited italic */
  font-weight: normal;
  line-height: 1.2;
  letter-spacing: 0;        /* prevent rt characters from spreading wide */
}

/* Tighten line height when ruby is present to prevent line height explosion */
article[data-lang] ruby + ruby,
article[data-lang] p:has(ruby) {
  line-height: 2.2;  /* needs extra room for rt annotation above base text */
}

/* ------------------------------------------------------------------
   Tappable word/character spans (.w)
   ------------------------------------------------------------------ */

/*
 * .w spans wrap individual characters (CJK) or words (Korean).
 * Goals:
 *   1. Invisible during normal reading — no borders, backgrounds, or
 *      color changes that disrupt reading flow.
 *   2. Subtle tap/hover feedback indicating interactivity.
 *   3. Highlight the active (tapped) span while the popup is open.
 *   4. Do not affect line metrics (no extra padding that shifts lines).
 */
.w {
  /*
   * cursor: pointer — signals interactivity on desktop hover.
   * On mobile this has no visible effect.
   */
  cursor: pointer;

  /*
   * -webkit-tap-highlight-color: transparent — suppress the native blue
   * tap flash on iOS/Android Safari; we supply our own feedback.
   */
  -webkit-tap-highlight-color: transparent;

  /*
   * border-radius on spans with background requires display:inline-block
   * or the background bleeds. We avoid background in resting state, so
   * no display change needed.
   */

  /*
   * transition for the active state feedback
   */
  transition: background-color 0.08s ease, color 0.08s ease;

  /* Ensure consistent box model */
  display: inline;
}

/* Hover feedback (desktop) */
@media (hover: hover) {
  .w:hover {
    background-color: var(--tap-highlight);
    border-radius: 2px;
  }
}

/* Active state — applied via JS when tap begins (touchstart / mousedown) */
.w.active {
  background-color: var(--tap-highlight);
  border-radius: 2px;
}

/* Selected word while popup is showing */
.w.selected {
  background-color: rgba(0, 80, 200, 0.18);
  border-radius: 2px;
}

/* Dark mode adjustments for .w */
@media (prefers-color-scheme: dark) {
  :root {
    --tap-highlight: rgba(100, 160, 255, 0.15);
  }

  .w.selected {
    background-color: rgba(100, 160, 255, 0.22);
  }
}

/* ------------------------------------------------------------------
   Miscellaneous inline elements
   ------------------------------------------------------------------ */
article[data-lang] mark {
  background: var(--reader-mark);
  color: inherit;
  border-radius: 2px;
  padding: 0 0.15em;
}

article[data-lang] abbr[title] {
  text-decoration: underline dotted;
  cursor: help;
}

article[data-lang] sub,
article[data-lang] sup {
  font-size: 0.7em;
  line-height: 0;  /* prevent sub/sup from expanding line height */
}

/* ------------------------------------------------------------------
   Column break behavior for multi-column layout
   ------------------------------------------------------------------ */

/*
 * These rules help the browser avoid placing orphaned text at the
 * top or bottom of a column. Not all browsers respect these in
 * multi-column layout, but they degrade gracefully.
 */
article[data-lang] p {
  orphans: 2;
  widows: 2;
}
```

### 3.3 Typography Rationale

**Serif vs. sans-serif:** Serif chosen for long-form reading. Research consistently supports serif for print-like reading on high-DPI screens. The primary users are reading novels, not scanning documents.

**Font size 1.1rem:** Slightly larger than browser default (16px → ~17.6px) given the reading context — full-screen, mobile-first.

**Line height 1.8:** High line height aids CJK readability where characters are complex. Also critical for the `.w` tap targets — tighter lines make it harder to tap individual characters without hitting adjacent lines.

**No text-indent:** Modern ebook readers (Kindle, Apple Books) use paragraph gap, not first-line indent. Indent + gap is redundant.

---

## 4. Ruby / Furigana

Ruby markup is used in Japanese for furigana (hiragana phonetic guides over kanji) and in annotated Chinese texts for pinyin or bopomofo.

### 4.1 Preservation

Ruby elements **must be preserved** through the normalization pipeline:
- `<ruby>`, `<rb>`, `<rt>`, `<rp>`, `<rtc>` — all in the allowlist (§2.2)
- `<rb>` is formally obsolete in HTML5 but widely used in EPUB; keep it for compatibility

### 4.2 Interaction with `.w` wrapping

**Problem:** If we naively wrap every CJK character in `<span class="w">`, we will break ruby structure:

```html
<!-- Original EPUB -->
<ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>

<!-- Wrong: wrapping rb characters inside ruby -->
<ruby><span class="w">漢</span><rt>かん</rt><span class="w">字</span><rt>じ</rt></ruby>
```

The `<span class="w">` inside `<ruby>` is valid HTML but can confuse browser ruby layout algorithms, causing `<rt>` to detach from its base.

**Recommendation:** Do not apply `.w` wrapping inside `<ruby>` elements. The entire `<ruby>` element should itself be tappable as a unit — tapping it looks up the full kanji word (e.g., "漢字"), not individual characters.

```html
<!-- Correct approach -->
<span class="w" data-lookup="漢字">
  <ruby>漢<rt>かん</rt>字<rt>じ</rt></ruby>
</span>
```

Pipeline logic: when processing CJK character wrapping, detect if a text node is a descendant of `<ruby>` — if so, skip wrapping. Instead, wrap the `<ruby>` element itself (as a sibling-level `.w` span after ruby processing is complete).

### 4.3 RT Styling

The `rt` CSS in §3.2 uses a sans-serif font stack for ruby text. This is intentional: furigana/pinyin annotations are gloss text, not body text. Sans-serif at small sizes is more legible than serif. Using a different font also creates visual contrast between base text (serif) and annotation (sans-serif).

The `line-height: 2.2` on paragraphs containing ruby is important. Without it, ruby annotations overlap the line above, making text unreadable.

### 4.4 Complex Ruby (`<rtc>`)

Some annotated Chinese EPUBs use double-sided ruby with `<rtc>` (ruby text container), providing both pinyin above and semantic annotation below. This is rare but valid. Keep `<rtc>` in the allowlist. The CSS `ruby-position: under` can be applied to `rtc > rt` for below-base annotations, but leave this as a future concern — no special handling needed in v1.

---

## 5. Vertical Writing Mode — Forward Compatibility

Vertical writing mode (`writing-mode: vertical-rl`) is explicitly out of scope for v1. However, the design should not create gratuitous obstacles to adding it later.

### 5.1 What the v1 Pipeline Does

**Strip `writing-mode` from inline styles.** In v1, EPUB files may arrive with `writing-mode: vertical-rl` in their inline styles (common in Japanese EPUBs). These are stripped by the inline style normalization (§1.2). This means vertical EPUBs render horizontally in v1 — which is a known limitation, not a bug.

**Do not add `writing-mode` CSS** to the reader stylesheet in v1.

### 5.2 Forward-Compatible Architecture

When vertical writing mode is added later, it should be activated at the `<article>` level via a data attribute, not by per-element styles:

```html
<article data-lang="ja" data-writing-mode="vertical">
```

This means:
- The pipeline should detect vertical-mode EPUBs (by presence of `writing-mode: vertical-rl` in the EPUB's OPF or CSS) and set `data-writing-mode="vertical"` on the article element.
- The reader CSS adds a block: `article[data-writing-mode="vertical"] { writing-mode: vertical-rl; }`.
- All other CSS should be authored such that it degrades cleanly under vertical layout.

### 5.3 CSS Properties to Avoid Hardcoding

These properties have logical equivalents that work in both horizontal and vertical contexts:

| Physical (avoid) | Logical (prefer) | Applies to |
|---|---|---|
| `margin-left` | `margin-inline-start` | Indentation |
| `padding-left` | `padding-inline-start` | Blockquote, lists |
| `width: 60%` (on `<hr>`) | `inline-size: 60%` | Separators |
| `border-left` | `border-inline-start` | Blockquote decoration |
| `text-align: left` | `text-align: start` | Default alignment |

**v1 decision:** Use physical properties for v1 (wider support, simpler). When vertical mode is added, audit and replace with logical equivalents at that time. The `data-writing-mode` attribute pattern means the upgrade is isolated to CSS, not the pipeline.

### 5.4 Multi-Column Layout in Vertical Mode

The existing CSS multi-column pagination uses:
```css
.chapter-content {
  column-width: 100vw;
  height: 100vh;
}
```

In vertical mode, columns stack left-to-right (manga-style) requires:
```css
.chapter-content[data-writing-mode="vertical"] {
  column-width: 100vh;   /* note: height becomes the inline dimension */
  height: 100vw;
  writing-mode: vertical-rl;
}
```

And navigation reverses direction (right→next page, left→previous). These are v2 concerns but worth noting.

---

## 6. Image Handling — Critical Gaps

### 6.1 Internal Image Path Rewriting

EPUB images are stored as entries inside the ZIP archive (e.g., `OEBPS/images/cover.jpg`). Chapter HTML references them with relative paths (e.g., `src="../images/cover.jpg"`). After the server extracts chapter HTML, these relative paths are broken — the browser cannot resolve them.

**Solution:** The normalization pipeline must rewrite all `<img src>` attributes to a server-proxied endpoint:

```
/book/:id/asset/:path
```

Where `:path` is the EPUB-internal path (resolved relative to the chapter's OPF location). The server streams the image file directly from the EPUB ZIP on request.

This requires a new endpoint in the API design (not currently in the design doc). The chapter pipeline:
1. Resolve `src` attribute relative to the chapter's location within the EPUB spine
2. Rewrite to `/book/{book_id}/asset/{resolved_internal_path}`

Inlining as `data:` URIs is an alternative but bloats chapter HTML and makes the N+1–N+5 lookahead buffer very memory-heavy for illustrated books.

### 6.2 SVG Image Wrapper Handling

Many EPUB generators emit images wrapped in SVG for dimension control:

```html
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400">
  <image xlink:href="../images/illustration.jpg" width="600" height="400"/>
</svg>
```

If the normalization pipeline strips all SVG (as proposed for v1 security simplicity), these images silently disappear — a significant content loss for illustrated books.

**Recommended approach:** Detect the specific SVG-as-image-wrapper pattern and convert it to a plain `<img>`:

| Pattern | Action |
|---|---|
| `<svg>` containing only a single `<image>` child | Convert to `<img src="{href}" alt="">` then strip svg/image |
| `<svg>` containing paths, text, circles, rects, etc. | Strip entirely in v1 (general SVG too complex) |
| Nested SVG | Strip entirely |

The conversion preserves image content while avoiding the complexity of SVG rendering. The resulting `<img>` then goes through normal image path rewriting (§6.1) and dimension stripping (§1.2g).

---

## 7. Normalization Pipeline — Summary

The complete updated pipeline step 3 (replacing the current step 3):

```
Step 3: Normalize HTML fragment
  3a. Security sanitization:
      - Strip <script>, <style>, on* attrs, javascript: hrefs
      - Strip <iframe>, <object>, <embed>
  3b. Structural element stripping:
      - Strip <head> (already in existing pipeline)
      - Unwrap <font> tags → text content
      - Strip <nav>, unknown media elements
  3c. Attribute normalization:
      - Strip class, align, valign, hspace, vspace attrs
      - Strip width/height on <img>
      - Apply inline style allowlist (keep only: font-style, font-weight,
        vertical-align:sub/super)
      - Strip data-* attributes (pipeline adds its own later)
  3d. <br> normalization:
      - Collapse runs of 2+ consecutive <br> into paragraph breaks
  3e. SVG image wrapper conversion:
      - <svg> containing single <image> child → convert to <img>
      - Other SVG → strip entirely
  3f. Image src rewriting:
      - Resolve relative paths against chapter's OPF location
      - Rewrite to /book/{book_id}/asset/{internal_path}
  3g. Apply tag allowlist:
      - All elements not in allowlist: unwrap inline, replace block with div
Step 4: Wrap tappable units (existing, with ruby-awareness fix from §4.2)
Step 5: Return <article data-lang="..."> fragment
```

---

## 8. Key Design Decisions

| Decision | Choice | Alternative | Reason |
|---|---|---|---|
| Inline style handling | Allowlist (semantic only) | Strip all | Preserves italic for foreign words; strip-all loses meaningful emphasis |
| `class` attributes | Strip all incoming | Keep structural | EPUB classes reference stripped stylesheets — keeping them is dead weight |
| `<br>` normalization | Collapse 2+ to `<p>` break | Strip all | Single `<br>` in poetry/dialogue is meaningful |
| Font stack | System serif, CJK-first | Web font | Zero requests, OS-level quality fonts, no FOUT |
| Ruby interaction with `.w` | Wrap `<ruby>` as unit | Wrap base chars | Prevents ruby layout breakage; kanji+furigana is a natural lookup unit |
| `writing-mode` in v1 | Strip from inline styles | Pass through | Can't support vertical mode yet; enables clean v2 upgrade path |
| `svg` in v1 | Strip | Keep | EPUB SVG illustrations need viewport/coordinate handling; too complex for v1 |
| Dark mode | CSS `prefers-color-scheme` | JS toggle | Zero JS, respects OS preference, no flash on load |
