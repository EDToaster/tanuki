# Reading Progress & Persistence Design

**Date:** 2026-04-03  
**Status:** Proposal  
**Author:** explorer-progress  
**Related:** [2026-04-03-ebook-reader-design.md](./2026-04-03-ebook-reader-design.md)

---

## Summary

The v1 ebook reader design marks "reading progress sync across devices" and "annotations/highlights that persist" as out of scope — but says nothing about *local* progress. Both features are achievable in the browser with zero backend changes. This document proposes a minimal `localStorage`-based reading progress system and evaluates whether annotations should follow the same path.

**Recommendation in brief:**
- **Reading progress** → `localStorage`. Simple, synchronous, near-zero complexity. Perfect fit for tiny, frequently-updated state.
- **Annotations/highlights** → Do *not* add in v1. If added in v2, use `localStorage` initially, with a clear upgrade path to `IndexedDB` if storage pressure becomes a problem.

---

## 1. Reading Progress Design

### 1.1 What to Store

Reading progress requires three pieces of information per book:

| Field | Type | Purpose |
|-------|------|---------|
| `chapterId` | `number` | Index into the OPF spine (0-based) |
| `pageIndex` | `number` | Rendered page within that chapter |
| `updatedAt` | `string` (ISO 8601) | For display ("last read 3 days ago") |

**Important caveat on `pageIndex`:** The design uses CSS multi-column pagination where page count is computed dynamically as `Math.round(container.scrollWidth / window.innerWidth)`. This means `pageIndex` is **viewport-dependent** — the same position in the text may be page 3 on a phone and page 1 on a tablet. `chapterId` is stable and should be treated as the reliable anchor. `pageIndex` is a best-effort within-chapter offset.

### 1.2 Storage Schema

```
localStorage key:   progress:{bookId}
localStorage value: JSON string
```

```json
{
  "chapterId": 4,
  "pageIndex": 2,
  "updatedAt": "2026-04-03T10:30:00.000Z"
}
```

- `bookId` is the slug derived from the EPUB filename (matches the `id` field in `/library` response). Example: `progress:the-three-body-problem`.
- One key per book. Overwritten on every page turn.
- No separate index key needed — the `progress:` prefix enables cheap enumeration.

### 1.3 When to Save

Save progress **on every page turn** (swipe or arrow key), not on `beforeunload`. Rationale:

- `beforeunload` and `pagehide` are unreliable on mobile — iOS Safari may not fire them when the user switches apps or the browser tab is killed.
- Writing a small JSON string on every page navigation is synchronous and completes in microseconds — no performance concern.
- The last valid position is always persisted; there is no "lost progress" window.

```
User swipes → page advances → progress saved → UI updates
```

### 1.4 Restoring Position on Book Open

When a user taps a book in the library:

1. Read `localStorage.getItem(`progress:${bookId}`)`.
2. If `null`, start at chapter 0, page 0 (normal open).
3. If present, parse the JSON:
   - Load chapter `chapterId` (which may already be in the lookahead buffer).
   - After rendering, navigate to `pageIndex`.
   - **Clamp** `pageIndex` to `[0, pageCount - 1]` — if the viewport changed since the last read, the stored page may be out of range. This silently corrects without user-visible error.
4. No loading indicator needed — this is a synchronous read that happens before the first render.

### 1.5 "Continue Reading" Indicator in Library View

When rendering the library grid, check progress for each book:

```
For each book in library response:
  → localStorage.getItem(`progress:${book.id}`)
  → If present: render badge on cover card
```

**Badge design (proposed):**

```
┌─────────────────────┐
│  [cover image]      │
│                     │
│  ╔═══════════════╗  │
│  ║ Ch 5 · p 3   ║  │  ← badge, bottom-left of card
│  ╚═══════════════╝  │
│  三体               │
│  刘慈欣             │
└─────────────────────┘
```

- Badge text: `Ch {N} · p {M}` (short, fits small covers)
- Optional secondary line: relative time from `updatedAt` ("3 days ago")
- Badge is purely decorative — tapping the card always shows the restore prompt or silently restores

**Restore prompt (optional):** When opening a book with saved progress, show a brief dismissible banner:

```
┌──────────────────────────────────────────┐
│ Continue from Chapter 5, page 3?  [Yes] [Start over] │
└──────────────────────────────────────────┘
```

This is user-friendly but adds a few lines of UI code. Alternatively, always restore silently and add a "Start from beginning" option in a header menu. Both are valid; the banner is more discoverable.

### 1.6 Data Size Estimate

- Per-book entry: ~120 bytes (JSON string with three fields)
- 500 books read: ~60 KB
- 5,000 books read: ~600 KB

This is trivial — localStorage's ~5 MB limit is not a concern for reading progress alone.

### 1.7 Clearing Progress

- "Start over" action: `localStorage.removeItem(`progress:${bookId}`)`.
- No bulk clear needed for MVP, but trivial to add: iterate all keys, delete those matching `progress:`.

---

## 2. Annotations / Highlights Evaluation

### 2.1 What an Annotation Entry Contains

A minimal annotation:

```json
{
  "id": "a1b2c3d4",
  "bookId": "the-three-body-problem",
  "chapterId": 4,
  "selectedText": "三体",
  "note": "Title of the book — literally 'Three-Body'",
  "color": "yellow",
  "createdAt": "2026-04-03T10:31:00.000Z"
}
```

Average size: ~300–600 bytes per annotation (more with longer selected text or notes).

### 2.2 Storage Size Projections

| Usage level | Annotations | Total size |
|-------------|-------------|------------|
| Light | 200 | ~120 KB |
| Moderate | 2,000 | ~1.2 MB |
| Heavy | 8,000 | ~4.8 MB |
| Very heavy | 10,000+ | Exceeds 5 MB limit |

A heavy reader of dense literary texts could realistically accumulate 5,000–10,000+ annotations across a library. `localStorage` becomes unsafe at that scale because **all annotations share the 5 MB origin budget with all other data** including reading progress and anything else the origin stores.

### 2.3 Structural Problems with localStorage for Annotations

Beyond size, there is a structural problem:

- Annotations for a book would be stored as a single JSON array: `annotations:{bookId}`.
- To add one annotation, you must read the entire array, push the new entry, and write the entire array back.
- For a book with 2,000 annotations, this is a repeated ~1.2 MB read-serialize-write cycle on the main thread.
- Filtering annotations by chapter requires loading all annotations for the book.

This is not a blocking problem for MVP (most books won't have thousands of annotations) but it is a clear architectural ceiling.

### 2.4 Recommendation: Do Not Add Annotations in v1

The design doc's choice to leave annotations out of scope is correct. Reasons:

1. **Annotations require a position system** that the current design lacks. To highlight text across a chapter page turn, you need stable text range anchors (e.g., XPath + character offsets). The current design has no such anchoring — `pageIndex` is ephemeral, and the chapter HTML is a re-processed EPUB fragment. Building annotation anchoring correctly is a substantial feature.

2. **Storage headroom**: The 5 MB localStorage limit is not a comfortable fit for a feature designed to accumulate unboundedly.

3. **Complexity**: Rendering highlights back into the paginated multi-column view requires either re-injecting `<mark>` spans into the EPUB HTML or overlaying absolutely-positioned elements that survive page translation — both are non-trivial.

**If annotations are added in v2**, use `IndexedDB` from the start. Do not start with `localStorage` and migrate later — migrating user annotation data is painful. The recommendation is: skip localStorage for annotations entirely, use IndexedDB with a small hand-rolled wrapper (no library required — see §3.4 below).

---

## 3. localStorage vs IndexedDB Trade-offs

### 3.1 Comparison Table

| Property | localStorage | IndexedDB |
|----------|-------------|-----------|
| **API style** | Synchronous | Asynchronous (Promise-based) |
| **Storage limit** | ~5 MB per origin | 50 MB+ (typically 50–80% of free disk, browser-dependent) |
| **Data types** | Strings only (manual JSON) | Structured objects, Blobs, ArrayBuffers |
| **Querying** | Key prefix iteration only | Indexes, cursors, range queries |
| **Transaction support** | None | Full ACID transactions |
| **Main thread blocking** | Yes (reads/writes block) | No (fully async) |
| **Browser support** | Universal | Universal (all modern browsers) |
| **Persistence** | Clears under storage pressure (Safari) | Can request `navigator.storage.persist()` |
| **Simplicity** | Very simple | Complex native API; simple with idb-keyval |
| **Fit for reading progress** | ✅ Excellent | Overkill |
| **Fit for annotations** | ⚠️ Marginal (size risk) | ✅ Excellent |

### 3.2 The Synchronous Trap

localStorage's synchronous API is often cited as a risk (blocking the main thread). For reading progress this is not a real concern:

- A single `getItem` / `setItem` call on a 120-byte value is unmeasurable at runtime.
- Blocking only becomes a problem with large values (KB+) or high-frequency writes.
- Reading progress: small, infrequent. No concern.

If the pattern were to write large blobs (e.g., caching chapter HTML), the synchronous API would be a real problem. But that is handled by the in-memory `Map` buffer described in the existing design — not localStorage.

### 3.3 Safari's Storage Eviction Behavior

Safari aggressively evicts localStorage data after 7 days of non-use for a given origin under storage pressure. This applies to **all** browsers in private/incognito mode too.

For reading progress this is an acceptable risk — losing your "continue reading" position is annoying but not catastrophic. The reader still opens the book; it just starts at chapter 0.

For annotations, eviction is unacceptable. This is another reason annotations should use IndexedDB with `navigator.storage.persist()` if/when they are built.

### 3.4 Hand-rolling a Minimal IndexedDB Wrapper (for Future Reference)

The design philosophy says "no client-side dependencies." The native IndexedDB API is verbose but manageable with a ~30-line wrapper. No library needed:

```
open → createObjectStore → put/get/getAll
```

This is worth noting for when annotations are eventually built: the existing "no dependencies" constraint does not force you into `localStorage`. A small inline wrapper is consistent with the project's philosophy.

### 3.5 When to Choose Each

| Use case | Storage choice | Reason |
|----------|---------------|--------|
| Reading progress (chapter + page) | `localStorage` | Tiny, synchronous, trivially simple |
| Wiktionary lookup cache (future) | `localStorage` or `sessionStorage` | Short-lived, per-session cache, small |
| Chapter HTML cache (current design) | In-memory `Map` | Already designed this way; correct |
| Annotations/highlights (future v2) | `IndexedDB` | Potentially large, structured queries needed |
| Book metadata cache (future) | `IndexedDB` or `localStorage` | Depends on size; library metadata is small |

---

## 4. Concrete Implementation Sketch

This section describes the design without code, as a specification for implementation.

### 4.1 New JS Module: `progress.js`

A standalone module (no imports) providing:

- `saveProgress(bookId, chapterId, pageIndex)` — writes to localStorage
- `loadProgress(bookId)` → `{ chapterId, pageIndex, updatedAt } | null` — reads from localStorage
- `clearProgress(bookId)` — removes the entry
- `getAllProgress()` → `Array<{ bookId, chapterId, pageIndex, updatedAt }>` — iterates all `progress:*` keys for the library view

### 4.2 Integration Points

**Reader view (reader.js / main index.html logic):**

1. On `openBook(bookId)`: call `loadProgress(bookId)`. If present, load that chapter and navigate to that page after render. If absent, start at chapter 0, page 0.
2. On every `goToPage()` / `goToChapter()`: call `saveProgress(bookId, currentChapterId, currentPageIndex)`.

**Library view:**

1. After fetching `/library`, call `getAllProgress()` to get a map of `bookId → progress`.
2. For each book in the grid, look up its id in the progress map. If found, render the "Continue" badge.

### 4.3 Edge Cases

| Case | Handling |
|------|---------|
| Stored `pageIndex` > current `pageCount` | Clamp to `pageCount - 1` |
| Stored `chapterId` > `chapter_count` | Fall back to chapter 0, page 0 |
| localStorage quota exceeded | `setItem` throws a `QuotaExceededError`; catch it silently (progress save fails gracefully) |
| Private/incognito mode | `localStorage` may throw on write in some browsers; wrap in try/catch, degrade gracefully |
| Book EPUB replaced on disk (same filename) | `bookId` (slug) is the same, so stale progress loads. Acceptable for a home server context. |

---

## 5. Summary and Decision Matrix

| Decision | Choice | Confidence |
|----------|--------|-----------|
| Storage for reading progress | `localStorage` | High |
| Storage for annotations (if v2) | `IndexedDB` | High |
| When to save progress | Every page turn | High |
| Primary position anchor | `chapterId` | High |
| Secondary position anchor | `pageIndex` (clamped) | High |
| Add annotations in v1 | No | High |
| Show "continue reading" badge | Yes, per-book in library grid | High |
| Restore prompt vs silent restore | Either; silent + "Start over" option is cleaner | Medium |

---

## 6. Design Fit with Existing Constraints

- **Zero backend changes:** Yes. All state stays in the browser.
- **No framework, no bundler:** Yes. `progress.js` is a plain JS module with no imports.
- **No client-side dependencies:** Yes. `localStorage` is a browser built-in.
- **Touch targets ≥ 44px:** The library badge is decorative; no new tap targets needed.
- **Out-of-scope respect:** Reading progress sync *across devices* remains out of scope. This design is deliberately local-only.
