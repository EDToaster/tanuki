// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  books: [],
  current: null,   // { id, title, language, chapterCount }
  chapter: 0,
  page: 0,
  cache: new Map(), // chapterIndex → html string
  profile: null,   // current profile name
};

// ── Router ────────────────────────────────────────────────────────────────────
function navigate(url) {
  history.pushState({}, '', url);
  route();
}

function route() {
  const path = window.location.pathname;
  const bookMatch    = path.match(/^\/u\/([^\/]+)\/book\/([^\/]+)/);
  const profileMatch = path.match(/^\/u\/([^\/]+)\//);

  if (bookMatch)    return showReader(bookMatch[1], bookMatch[2]);
  if (profileMatch) return showLibrary(profileMatch[1]);
  showProfilePicker();
}

window.addEventListener('popstate', route);
document.addEventListener('DOMContentLoaded', route);

// ── View helpers ──────────────────────────────────────────────────────────────
function showAppView() {
  document.getElementById('app').style.display = 'block';
  document.getElementById('reader-view').classList.remove('active');
}

function showReaderView() {
  document.getElementById('app').style.display = 'none';
  document.getElementById('reader-view').classList.add('active');
}

// ── Profile picker ────────────────────────────────────────────────────────────
async function showProfilePicker() {
  const last = localStorage.getItem('lastProfile');
  if (last) { navigate(`/u/${last}/`); return; }

  showAppView();
  const profiles = await fetch('/api/profiles').then(r => r.json()).catch(() => []);

  document.getElementById('app').innerHTML = `
    <div class="picker-screen">
      <h1>Who's reading?</h1>
      <div class="picker-grid" id="picker-grid"></div>
      <button id="new-profile-btn" class="new-profile-btn">+ New profile</button>
    </div>`;

  const grid = document.getElementById('picker-grid');
  profiles.forEach(p => {
    const btn = document.createElement('button');
    btn.className = 'profile-btn';
    btn.textContent = p.name;
    btn.addEventListener('click', () => selectProfile(p.name));
    grid.appendChild(btn);
  });

  document.getElementById('new-profile-btn').addEventListener('click', async () => {
    const name = prompt('Profile name (letters, digits, hyphens, underscores):');
    if (!name) return;
    const r = await fetch('/api/profiles', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    });
    if (r.ok) selectProfile(name);
    else alert((await r.json()).error || 'Error creating profile');
  });
}

function selectProfile(name) {
  localStorage.setItem('lastProfile', name);
  navigate(`/u/${name}/`);
}

// ── Library ───────────────────────────────────────────────────────────────────
async function showLibrary(profile) {
  showAppView();
  document.getElementById('app').innerHTML = '<div class="library-view"><div class="book-grid" id="book-grid"></div></div>';

  const [books, allProgress] = await Promise.all([
    fetch('/library').then(r => r.json()).catch(() => []),
    fetch(`/api/u/${profile}/progress`).then(r => r.ok ? r.json() : []).catch(() => [])
  ]);

  const progressMap = new Map(allProgress.map(p => [p.book_id, p]));
  const grid = document.getElementById('book-grid');

  if (books.length === 0) {
    grid.innerHTML = '<p class="empty-state">No books found. Add EPUBs to the books/ directory.</p>';
    return;
  }

  books.forEach(book => {
    const card = document.createElement('div');
    card.className = 'book-card';
    card.style.position = 'relative';

    // Use DOM API / textContent — never innerHTML with server data.
    const img = document.createElement('img');
    img.src = book.cover_url;
    img.alt = '';
    img.loading = 'lazy';
    img.onerror = () => { img.style.background = '#ddd'; };

    const titleEl = document.createElement('div');
    titleEl.className = 'book-title';
    titleEl.textContent = book.title;

    const authorEl = document.createElement('div');
    authorEl.className = 'book-author';
    authorEl.textContent = book.author;

    card.append(img, titleEl, authorEl);

    const prog = progressMap.get(book.id);
    if (prog) {
      const badge = document.createElement('div');
      badge.className = 'continue-badge';
      badge.textContent = `Ch ${prog.chapter_id + 1} · p ${prog.page_index + 1}`;
      card.appendChild(badge);
    }

    card.addEventListener('click', () => navigate(`/u/${profile}/book/${book.id}`));
    grid.appendChild(card);
  });
}

// ── Reader ────────────────────────────────────────────────────────────────────
async function showReader(profile, bookId) {
  showReaderView();

  state.profile = profile;
  state.chapter = 0;
  state.page = 0;
  state.cache.clear();

  // Fetch library to find book metadata
  const books = await fetch('/library').then(r => r.json()).catch(() => []);
  const book = books.find(b => String(b.id) === String(bookId));

  if (!book) {
    document.getElementById('chapter-container').innerHTML =
      '<p style="padding:20px;color:#888">Book not found.</p>';
    return;
  }

  state.current = {
    id: book.id,
    title: book.title,
    language: book.language,
    chapterCount: book.chapter_count,
  };
  document.getElementById('book-title').textContent = book.title;

  // Restore progress: try backend first, fall back to localStorage
  let startChapter = 0, startPage = 0;
  try {
    const r = await fetch(`/api/u/${profile}/progress/${bookId}`);
    if (r.ok) {
      const p = await r.json();
      startChapter = p.chapter_id;
      startPage    = p.page_index;
    }
  } catch {
    const local = JSON.parse(localStorage.getItem(`progress:${bookId}`) || 'null');
    if (local) { startChapter = local.chapter_id; startPage = local.page_index; }
  }

  // Pass startPage so the initial rAF sets the correct page (avoids race with rAF resetting to 0)
  await loadChapter(startChapter, startPage);
  prefetchAhead(startChapter);
}

// ── Progress save — dual-write on every page/chapter change ───────────────────
function saveProgress(chapterId, pageIndex) {
  const bookId = state.current?.id;
  const profile = state.profile;
  if (!bookId || !profile) return;

  const payload = JSON.stringify({chapter_id: chapterId, page_index: pageIndex});
  // Always write localStorage immediately (offline-safe)
  localStorage.setItem(`progress:${bookId}`, payload);
  // Try backend (fire-and-forget)
  fetch(`/api/u/${profile}/progress/${bookId}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: payload
  }).catch(() => {}); // silently ignore failures
}

// ── Chapter loading ───────────────────────────────────────────────────────────
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

async function loadChapter(index, initialPage = 0) {
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
    setPage(content, initialPage);
  });
}

function pageCount(content) {
  // Hi-DPI fix: scrollWidth is an integer; window.innerWidth is a float on hi-DPI displays.
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

// ── Navigation ────────────────────────────────────────────────────────────────
async function nextPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page < pageCount(content) - 1) {
    setPage(content, state.page + 1);
    saveProgress(state.chapter, state.page);
  } else if (state.chapter < state.current.chapterCount - 1) {
    await loadChapter(state.chapter + 1);
    prefetchAhead(state.chapter);
    saveProgress(state.chapter, state.page);
  }
}

async function prevPage() {
  const content = currentContent();
  if (!content) return;
  if (state.page > 0) {
    setPage(content, state.page - 1);
    saveProgress(state.chapter, state.page);
  } else if (state.chapter > 0) {
    await loadChapter(state.chapter - 1);
    const c = currentContent();
    if (c) setPage(c, pageCount(c) - 1);
    saveProgress(state.chapter, state.page);
  }
}

// ── Touch / keyboard ──────────────────────────────────────────────────────────
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
  const profile = state.profile;
  if (profile) navigate(`/u/${profile}/`);
  else navigate('/');
});

// ── Dictionary ────────────────────────────────────────────────────────────────
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
