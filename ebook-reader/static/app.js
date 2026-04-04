// ── State ────────────────────────────────────────────────────────────────────
const state = {
  books: [],
  current: null,   // { id, title, language, chapterCount }
  chapter: 0,
  page: 0,
  cache: new Map(), // chapterIndex → html string
};

// ── Views ────────────────────────────────────────────────────────────────────
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Library ──────────────────────────────────────────────────────────────────
async function loadLibrary() {
  const res = await fetch('/library');
  state.books = await res.json();
  renderLibrary();
}

function renderLibrary() {
  const grid = document.getElementById('book-grid');
  grid.innerHTML = '';
  if (state.books.length === 0) {
    const msg = document.createElement('p');
    msg.style.cssText = 'padding:16px;color:#888';
    msg.textContent = 'No books found. Add EPUBs to the books/ directory.';
    grid.appendChild(msg);
    return;
  }
  state.books.forEach(book => {
    const card = document.createElement('div');
    card.className = 'book-card';

    // Security fix #2: use DOM API / textContent — never innerHTML with server data.
    const img = document.createElement('img');
    img.src = book.cover_url;
    img.alt = '';
    img.loading = 'lazy';
    img.onerror = () => { img.style.background = '#ddd'; };

    const titleEl = document.createElement('div');
    titleEl.className = 'book-title';
    titleEl.textContent = book.title;    // textContent, not innerHTML

    const authorEl = document.createElement('div');
    authorEl.className = 'book-author';
    authorEl.textContent = book.author;  // textContent, not innerHTML

    card.append(img, titleEl, authorEl);
    card.addEventListener('click', () => openBook(book));
    grid.appendChild(card);
  });
}

loadLibrary();

// ── Reader ───────────────────────────────────────────────────────────────────
async function openBook(book) {
  state.current = {
    id: book.id,
    title: book.title,
    language: book.language,
    chapterCount: book.chapter_count,
  };
  state.chapter = 0;
  state.page = 0;
  state.cache.clear();
  document.getElementById('book-title').textContent = book.title;
  showView('reader-view');
  await loadChapter(0);
  prefetchAhead(0);
}

async function fetchChapter(index) {
  if (state.cache.has(index)) return state.cache.get(index);
  const res = await fetch(`/book/${state.current.id}/chapter/${index}`);
  if (!res.ok) return null;
  const html = await res.text();
  state.cache.set(index, html);
  evictCache(index);
  return html;
}

function evictCache(current) {
  for (const key of state.cache.keys()) {
    if (key < current - 2) state.cache.delete(key);
  }
}

function prefetchAhead(from) {
  const total = state.current.chapterCount;
  for (let i = from + 1; i <= Math.min(from + 5, total - 1); i++) {
    fetchChapter(i); // fire and forget
  }
}

async function loadChapter(index) {
  const html = await fetchChapter(index);
  if (html === null) return;
  state.chapter = index;
  state.page = 0;

  const container = document.getElementById('chapter-container');
  container.innerHTML = '';

  const content = document.createElement('div');
  content.className = 'chapter-content';
  // Content comes from server's pre-sanitized chapter endpoint
  content.innerHTML = html;
  container.appendChild(content);

  requestAnimationFrame(() => {
    updatePageIndicator(content);
    setPage(content, 0);
  });
}

function pageCount(content) {
  // Hi-DPI fix (security fix #5):
  // scrollWidth is an integer; window.innerWidth is a float on hi-DPI displays.
  // Using (scrollWidth + 1) adds a 1px tolerance to avoid off-by-one errors.
  return Math.max(1, Math.round((content.scrollWidth + 1) / window.innerWidth));
}

function setPage(content, page) {
  const total = pageCount(content);
  state.page = Math.max(0, Math.min(page, total - 1));
  content.style.transform = `translateX(${-state.page * window.innerWidth}px)`;
  updatePageIndicator(content);
}

function updatePageIndicator(content) {
  const el = document.getElementById('page-indicator');
  const total = pageCount(content);
  const ch = state.chapter + 1;
  const chTotal = state.current?.chapterCount ?? '?';
  el.textContent = `Ch ${ch}/${chTotal}  ·  ${state.page + 1}/${total}`;
}

function currentContent() {
  return document.querySelector('.chapter-content');
}

// ── Navigation ───────────────────────────────────────────────────────────────
async function nextPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page < pageCount(content) - 1) {
    setPage(content, state.page + 1);
  } else if (state.chapter < state.current.chapterCount - 1) {
    await loadChapter(state.chapter + 1);
    prefetchAhead(state.chapter);
  }
}

async function prevPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page > 0) {
    setPage(content, state.page - 1);
  } else if (state.chapter > 0) {
    await loadChapter(state.chapter - 1);
    const c = currentContent();
    if (c) setPage(c, pageCount(c) - 1);
  }
}

// ── Touch / keyboard ─────────────────────────────────────────────────────────
let touchStartX = 0;
document.getElementById('reader-view').addEventListener('touchstart', e => {
  touchStartX = e.changedTouches[0].clientX;
}, { passive: true });

document.getElementById('reader-view').addEventListener('touchend', e => {
  const dx = e.changedTouches[0].clientX - touchStartX;
  if (Math.abs(dx) > 40) dx < 0 ? nextPage() : prevPage();
});

document.addEventListener('keydown', e => {
  if (!document.getElementById('reader-view').classList.contains('active')) return;
  if (e.key === 'ArrowRight') nextPage();
  if (e.key === 'ArrowLeft') prevPage();
});

document.getElementById('back-btn').addEventListener('click', () => {
  showView('library-view');
});

// ── Dictionary ───────────────────────────────────────────────────────────────
// Phase 1: direct Wiktionary lookup. Phase 3 replaces this with /api/dict.

async function lookupWord(word) {
  showPopup(word, null, null, null);

  const lang = state.current?.language ?? '';
  const url = `https://en.wiktionary.org/w/api.php?action=parse&page=${encodeURIComponent(word)}&prop=text&format=json&origin=*`;

  let pronunciation = '';
  let definitions = [];
  let networkError = false;

  try {
    const res = await fetch(url);
    const data = await res.json();
    if (data.error) throw new Error('not found');

    const html = data.parse.text['*'];
    const doc = new DOMParser().parseFromString(html, 'text/html');

    const langMap = { zh: 'Chinese', ko: 'Korean' };
    const targetLang = langMap[lang] ?? '';

    let langSection = null;
    doc.querySelectorAll('h2').forEach(h2 => {
      if (h2.textContent.includes(targetLang)) langSection = h2;
    });

    const root = langSection ? langSection.parentElement : doc.body;
    const pronEl = root.querySelector('.IPA, .pinyin, [class*="pron"]');
    pronunciation = pronEl?.textContent?.trim() ?? '';

    root.querySelectorAll('ol li').forEach(li => {
      const text = li.childNodes[0]?.textContent?.trim();
      if (text && text.length > 1) definitions.push(text);
    });

    if (!pronunciation && !definitions.length) throw new Error('no content');
  } catch (e) {
    if (e instanceof TypeError) networkError = true;
    definitions = networkError
      ? ['Network error — check connection.']
      : ['No Wiktionary entry found.'];
  }

  const sourceUrl = `https://en.wiktionary.org/wiki/${encodeURIComponent(word)}`;
  showPopup(word, pronunciation, definitions, sourceUrl);
}

function showPopup(word, pronunciation, definitions, sourceUrl) {
  document.getElementById('popup-word').textContent = word;
  document.getElementById('popup-pronunciation').textContent = pronunciation ?? 'Loading…';

  const defEl = document.getElementById('popup-definitions');
  if (definitions === null) {
    defEl.innerHTML = '<em>Loading…</em>';
  } else {
    defEl.innerHTML = definitions.length
      ? '<ol>' + definitions.slice(0, 5).map(d => `<li>${d}</li>`).join('') + '</ol>'
      : '';
  }

  const link = document.getElementById('popup-source-link');
  if (sourceUrl) {
    link.href = sourceUrl;
    link.textContent = 'Open in Wiktionary ↗';
    link.style.display = 'block';
  } else {
    link.style.display = 'none';
  }

  document.getElementById('lookup-popup').classList.remove('hidden');
}

function hidePopup() {
  document.getElementById('lookup-popup').classList.add('hidden');
  document.querySelectorAll('span.w.active').forEach(s => s.classList.remove('active'));
}

document.getElementById('popup-close').addEventListener('click', hidePopup);

document.getElementById('chapter-container').addEventListener('click', e => {
  const span = e.target.closest('span.w');
  if (span) {
    document.querySelectorAll('span.w.active').forEach(s => s.classList.remove('active'));
    span.classList.add('active');
    const word = span.dataset.lookup || span.innerText;
    lookupWord(word);
    e.stopPropagation();
    return;
  }
  hidePopup();
});

// ── Text selection pill ───────────────────────────────────────────────────────
const pill = document.getElementById('lookup-pill');
document.addEventListener('selectionchange', () => {
  const sel = window.getSelection();
  const text = sel?.toString().trim();
  if (!text) { pill.style.display = 'none'; return; }
  const range = sel.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  pill.style.display = 'block';
  pill.style.left = `${rect.left + rect.width / 2 - 40}px`;
  pill.style.top = `${rect.top - 44 + window.scrollY}px`;
  pill.classList.add('visible');
});

pill.addEventListener('click', () => {
  const text = window.getSelection()?.toString().trim();
  if (text) lookupWord(text);
  pill.classList.remove('visible');
});
