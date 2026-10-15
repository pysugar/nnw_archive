'use strict';

const $ = id => document.getElementById(id);
const els = Object.fromEntries([
  'stats', 'filters', 'q', 'feed', 'folder', 'unread', 'starred', 'all', 'page-size',
  'results', 'prev', 'next', 'page-label', 'result-count', 'filter-toggle',
  'filter-panel', 'filter-count', 'date-from', 'date-to', 'reset', 'workspace',
  'reader-stage', 'reader-state', 'reader-source', 'state-title', 'state-description',
  'retry-article', 'original', 'back', 'theme', 'font-smaller', 'font-larger', 'app-notice',
].map(id => [id, $(id)]));
let frame = $('article-frame');
const state = {
  page: 1, pages: 1, items: [], feeds: [], articleID: null, article: null,
  searchController: null, articleController: null, searchVersion: 0, articleVersion: 0,
  unread: false, starred: false,
};
const numberFormat = new Intl.NumberFormat('zh-CN');
const dateFormat = new Intl.DateTimeFormat('zh-CN', {year: 'numeric', month: '2-digit', day: '2-digit'});
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
const narrowScreen = window.matchMedia('(max-width: 760px)');

function preference(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`nnw-${key}`);
    localStorage.setItem(`nnw-${key}`, String(value));
  } catch { /* Reading still works when storage is unavailable. */ }
  return null;
}

const savedFontSize = Number(preference('font-size'));
let fontSize = savedFontSize >= 15 && savedFontSize <= 23 ? savedFontSize : 17;
let themeChoice = preference('theme');
if (!['light', 'dark'].includes(themeChoice)) themeChoice = null;

function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return '日期未知';
  const date = new Date(Number.isFinite(Number(value)) ? Number(value) * 1000 : value);
  return Number.isNaN(date.getTime()) ? '日期未知' : dateFormat.format(date);
}

function safeURL(value, base, protocols = ['http:', 'https:']) {
  if (!value) return null;
  try {
    const url = base ? new URL(value, base) : new URL(value);
    return protocols.includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

async function fetchJSON(url, signal) {
  const response = await fetch(url, {signal, headers: {Accept: 'application/json'}});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function showNotice(message) {
  els['app-notice'].textContent = message;
  els['app-notice'].hidden = !message;
}

function updateFilterUI() {
  for (const name of ['unread', 'starred']) els[name].setAttribute('aria-pressed', String(state[name]));
  els.all.setAttribute('aria-pressed', String(!state.unread && !state.starred));
  const count = ['feed', 'folder', 'date-from', 'date-to'].filter(name => els[name].value).length;
  els['filter-count'].textContent = count;
  els['filter-count'].hidden = !count;
  els['filter-toggle'].classList.toggle('has-filters', count > 0);
  els.reset.hidden = !(count || state.unread || state.starred || els.q.value.trim());
}

function queryParams() {
  const params = new URLSearchParams({page: String(state.page), page_size: els['page-size'].value});
  for (const [id, key] of [['q', 'q'], ['feed', 'feed_id'], ['folder', 'folder'], ['date-from', 'from'], ['date-to', 'to']]) {
    if (els[id].value.trim()) params.set(key, els[id].value.trim());
  }
  if (state.starred) params.set('starred', '1');
  if (state.unread) params.set('unread', '1');
  return params;
}

function resetReader() {
  state.articleController?.abort();
  state.articleVersion += 1;
  state.articleID = null;
  state.article = null;
  els.workspace.classList.remove('reading');
  els.original.hidden = true;
  els.original.removeAttribute('href');
  els['reader-source'].textContent = '阅读空间';
  showReaderState('值得留下，也值得重读', '从左侧选择一篇文章，开始安静地阅读。\n也可以搜索关键词，找回曾经的灵感。');
}

function showReaderState(title, description, {loading = false, retry = false} = {}) {
  frame.hidden = true;
  frame.removeAttribute('srcdoc');
  els['reader-state'].hidden = false;
  els['state-title'].textContent = title;
  els['state-description'].textContent = description;
  els['retry-article'].hidden = !retry;
  els['reader-stage'].setAttribute('aria-busy', String(loading));
  updateFontButtons();
}

function renderLoading() {
  els.results.innerHTML = Array.from({length: 6}, () => '<div class="skeleton" aria-hidden="true"><span></span><span></span><span></span></div>').join('');
  els.results.setAttribute('aria-busy', 'true');
  els['result-count'].textContent = '正在查找文章…';
  els.prev.disabled = true;
  els.next.disabled = true;
}

function renderListState(title, description, retry = false) {
  const panel = document.createElement('div');
  panel.className = `list-state${retry ? ' error' : ''}`;
  panel.innerHTML = `<strong>${escapeHTML(title)}</strong><div>${escapeHTML(description)}</div>`;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'primary-button';
  button.textContent = retry ? '重试' : '清除筛选';
  button.addEventListener('click', retry ? () => search() : resetFilters);
  if (retry || !els.reset.hidden) panel.append(button);
  els.results.replaceChildren(panel);
}

function selectResult(articleID) {
  for (const button of els.results.querySelectorAll('.item')) {
    const active = button.dataset.articleId === articleID;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  }
}

function renderResults(payload) {
  state.page = payload.page;
  state.pages = payload.pages;
  state.items = payload.items;
  els['page-label'].textContent = `${state.page} / ${state.pages}`;
  els['result-count'].textContent = `${numberFormat.format(payload.total)} 篇文章`;
  els.prev.disabled = state.page <= 1;
  els.next.disabled = state.page >= state.pages;
  els.results.replaceChildren();
  if (!payload.items.length) {
    renderListState('这里还没有文章', '试试其他关键词，或调整订阅源和日期范围。');
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const item of payload.items) {
    const button = document.createElement('button');
    button.className = 'item';
    button.type = 'button';
    button.dataset.articleId = item.articleID;
    const feed = item.feedTitle || item.feedID || '未知订阅源';
    const indicators = [
      !item.read ? '<span class="unread-dot" aria-hidden="true"></span><span>未读</span>' : '<span>已读</span>',
      item.starred ? `${icon('star')}<span>星标</span>` : '',
      item.source_deleted_at ? '<span>历史保留</span>' : '',
    ].join('');
    button.innerHTML = `
      <span class="item-topline"><span class="feed-monogram" aria-hidden="true">${escapeHTML(Array.from(feed)[0] || 'N')}</span><span class="item-feed">${escapeHTML(feed)}</span><span class="item-time">${escapeHTML(formatDate(item.datePublished ?? item.dateArrived))}</span></span>
      <span class="item-title">${escapeHTML(item.title || '无标题文章')}</span>
      ${item.excerpt ? `<span class="item-excerpt">${escapeHTML(item.excerpt)}</span>` : ''}
      <span class="item-indicators">${indicators}</span>`;
    button.addEventListener('click', () => loadArticle(item.articleID));
    fragment.append(button);
  }
  els.results.append(fragment);
  selectResult(state.articleID);
  els.results.scrollTop = 0;
}

async function search({resetPage = false} = {}) {
  if (resetPage) state.page = 1;
  updateFilterUI();
  state.searchController?.abort();
  const controller = new AbortController();
  state.searchController = controller;
  const version = ++state.searchVersion;
  resetReader();
  renderLoading();
  try {
    const payload = await fetchJSON(`/api/search?${queryParams()}`, controller.signal);
    if (version !== state.searchVersion) return;
    renderResults(payload);
  } catch (error) {
    if (version !== state.searchVersion || error.name === 'AbortError') return;
    els['result-count'].textContent = '加载失败';
    els['page-label'].textContent = '— / —';
    renderListState('暂时无法加载文章', `请检查筛选条件或本地服务。${error.message}`, true);
  } finally {
    if (version === state.searchVersion) els.results.setAttribute('aria-busy', 'false');
  }
}

function resetFilters() {
  for (const name of ['q', 'feed', 'folder', 'date-from', 'date-to']) els[name].value = '';
  state.starred = false;
  state.unread = false;
  populateFeeds();
  search({resetPage: true});
}

function populateFeeds() {
  const selected = els.feed.value;
  els.feed.replaceChildren(new Option('全部订阅源', ''));
  for (const feed of state.feeds) {
    if (els.folder.value && feed.folder_path !== els.folder.value) continue;
    els.feed.append(new Option(`${feed.display_name || feed.feedID} · ${numberFormat.format(feed.article_count)}`, feed.feedID));
  }
  if ([...els.feed.options].some(option => option.value === selected)) els.feed.value = selected;
}

async function loadFeeds() {
  try {
    state.feeds = await fetchJSON('/api/feeds');
    populateFeeds();
    const folders = [...new Set(state.feeds.map(feed => feed.folder_path).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    for (const folder of folders) els.folder.append(new Option(folder, folder));
  } catch {
    showNotice('订阅源列表暂时无法读取；仍可使用关键词搜索。刷新页面可重试。');
  }
}

async function loadStats() {
  try {
    const data = await fetchJSON('/api/stats');
    const lastRun = data.last_run;
    const syncText = lastRun?.error ? '最近同步失败' : lastRun?.finished_at ? `最近同步 ${formatDate(lastRun.finished_at)}` : lastRun ? '同步尚未完成' : '尚未同步';
    els.stats.innerHTML = `<span><strong>${numberFormat.format(data.articles)}</strong>篇文章</span><span><strong>${numberFormat.format(data.feeds)}</strong>个订阅源</span><span title="原始库已移除，归档中仍然保留"><strong>${numberFormat.format(data.source_deleted)}</strong>篇历史保留</span><span class="sync-status">${escapeHTML(syncText)}</span>`;
  } catch { els.stats.textContent = '归档统计暂不可用'; }
}

function imageURL(value, base) {
  return safeURL(value, base) || (/^data:image\/(png|jpeg|gif|webp);base64,[a-z\d+/=\s]+$/i.test(value || '') ? value : null);
}

function srcsetURLs(value) {
  if (!value) return [];
  // Commas inside CDN URLs are legal; split at the descriptor, not at every comma.
  const entries = [...value.matchAll(/(?:^|,)\s*(\S+?)\s+(\d+(?:\.\d+)?[wx])(?=\s*,|\s*$)/g)];
  return entries.length ? entries.reverse().map(entry => entry[1]) : [value.trim()];
}

function imageCandidates(element, base) {
  const src = element.getAttribute('src');
  const lazy = ['data-src', 'data-original', 'data-lazy-src', 'data-original-src'].map(name => element.getAttribute(name));
  const responsive = ['srcset', 'data-srcset'].flatMap(name => srcsetURLs(element.getAttribute(name)));
  for (const source of element.closest('picture')?.querySelectorAll('source') || []) {
    responsive.push(...srcsetURLs(source.getAttribute('srcset') || source.getAttribute('data-srcset')));
  }
  const candidates = [element.getAttribute('data-canonical-src')];
  if (src?.startsWith('data:')) candidates.push(...lazy, ...responsive, src);
  else candidates.push(src, ...lazy, ...responsive);
  return [...new Set(candidates.map(value => imageURL(value, base)).filter(Boolean))].slice(0, 8);
}

// Feed markup is untrusted. Only the nonce-protected reader helper may run scripts.
function articleBody(article) {
  if (!article.contentHTML) {
    const text = article.contentText || article.markdown || article.summary;
    return text ? `<div class="plain-text">${escapeHTML(text)}</div>` : '<p class="content-empty">这篇文章没有保存正文，可以通过右上角的「阅读原文」访问来源。</p>';
  }
  const markup = article.contentHTML.trim().replace(/^<!\[CDATA\[([\s\S]*)\]\]>$/, '$1');
  const doc = new DOMParser().parseFromString(markup, 'text/html');
  const allowed = new Set('a abbr b blockquote br caption code col colgroup dd del details div dl dt em figcaption figure h1 h2 h3 h4 h5 h6 hr i img li mark ol p picture pre s section small source span strong sub summary sup table tbody td th thead tfoot time tr u ul video audio'.split(' '));
  const drop = new Set('script style iframe frame frameset object embed form input button textarea select option link meta base noscript svg math template'.split(' '));
  const base = safeURL(article.url) || safeURL(article.externalURL) || safeURL(article.feedURL);
  const images = new Map([...doc.body.querySelectorAll('img')].map(element => [element, imageCandidates(element, base)]));
  for (const element of [...doc.body.querySelectorAll('*')]) {
    const tag = element.localName;
    if (drop.has(tag)) { element.remove(); continue; }
    if (!allowed.has(tag)) { element.replaceWith(...element.childNodes); continue; }
    for (const attribute of [...element.attributes]) {
      const name = attribute.name;
      const value = attribute.value;
      element.removeAttribute(name);
      if (['alt', 'title', 'lang', 'datetime', 'id'].includes(name)) element.setAttribute(name, value);
      if (name === 'dir' && ['rtl', 'ltr', 'auto'].includes(value)) element.setAttribute(name, value);
      if (['colspan', 'rowspan', 'start'].includes(name) && /^\d{1,3}$/.test(value)) element.setAttribute(name, value);
      if (tag === 'a' && name === 'href') {
        const url = safeURL(value, base, ['http:', 'https:', 'mailto:']);
        if (url) element.setAttribute('href', url);
      }
      if (['source', 'video', 'audio'].includes(tag) && name === 'src') {
        const url = safeURL(value, base);
        if (url) element.setAttribute('src', url);
      }
    }
    if (tag === 'a') { element.setAttribute('target', '_blank'); element.setAttribute('rel', 'noopener noreferrer'); }
    if (tag === 'img') {
      const candidates = images.get(element);
      if (candidates.length) element.setAttribute('src', candidates[0]);
      element.setAttribute('data-image-candidates', JSON.stringify(candidates));
      element.setAttribute('loading', 'lazy');
      element.setAttribute('referrerpolicy', 'no-referrer');
    }
    if (['video', 'audio'].includes(tag)) { element.setAttribute('controls', ''); element.setAttribute('preload', 'none'); }
  }
  return doc.body.innerHTML;
}

function readerDocument(article) {
  const dark = document.documentElement.dataset.theme === 'dark';
  const [bg, ink, muted, line, accent, soft] = ['bg', 'ink', 'muted', 'line', 'accent', 'soft'].map(name => `var(--${name})`);
  const nonce = [...crypto.getRandomValues(new Uint8Array(18))].map(value => value.toString(16).padStart(2, '0')).join('');
  const helperURL = new URL('/static/reader.js', window.location.href).href;
  const authors = (article.authors || []).map(author => author.name).filter(Boolean).join('、');
  const metadata = [formatDate(article.datePublished ?? article.dateArrived), authors, article.starred ? '已加星标' : null].filter(Boolean);
  return `<!doctype html><html lang="zh-CN" data-theme="${dark ? 'dark' : 'light'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; img-src http: https: data:; media-src http: https:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><style>
    :root{color-scheme:light;--reader-size:${fontSize}px;--bg:#ffffff;--ink:#253733;--muted:#72817d;--line:#e4eae7;--accent:#25695a;--soft:#f4f7f5}
    :root[data-theme="dark"]{color-scheme:dark;--bg:#1c2522;--ink:#e1e9e4;--muted:#96aaa0;--line:#2e3a34;--accent:#9bd4b7;--soft:#24312a}
    *{box-sizing:border-box}[hidden]{display:none!important}html{background:${bg};color:${ink};overflow-wrap:anywhere;scrollbar-width:thin;scrollbar-color:${line} transparent}
    body{margin:0;font:var(--reader-size)/1.95 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;-webkit-font-smoothing:antialiased}
    .document{max-width:800px;margin:auto;padding:48px 48px 96px}.article-feed{font-size:11px;font-weight:600;letter-spacing:.6px;color:${accent};margin:0 0 14px}
    .article-title{font:600 32px/1.5 "Songti SC","Noto Serif CJK SC",ui-serif,Georgia,serif;letter-spacing:.2px;margin:0 0 20px}
    .article-meta{display:flex;flex-wrap:wrap;gap:8px 20px;font:12px/1.6 -apple-system,BlinkMacSystemFont,sans-serif;color:${muted};padding-bottom:28px;border-bottom:1px solid ${line};margin-bottom:30px}
    .retained{display:block;color:${muted};font-size:11px;padding:12px 15px;background:${soft};border-radius:5px;margin-bottom:25px}
    p,ul,ol,dl,figure,blockquote,pre,table{margin:0 0 1.4em}h1,h2,h3,h4,h5,h6{line-height:1.5;margin:1.7em 0 .7em;font-weight:650}h1{font-size:1.6em}h2{font-size:1.35em}h3{font-size:1.15em}
    .article-content>:first-child{margin-top:0}a{color:${accent};text-underline-offset:4px}img,video,picture{max-width:100%;height:auto;border-radius:6px}audio{max-width:100%}img{display:block;margin:1.4em auto}
    pre{overflow:auto;padding:18px;background:${soft};border:1px solid ${line};border-radius:6px;line-height:1.65;font-size:.82em;white-space:pre-wrap}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;background:${soft};border-radius:3px;padding:2px 4px}pre code{padding:0;font-size:inherit}
    blockquote{margin-left:0;margin-right:0;border-left:3px solid ${accent};padding:8px 20px;color:${muted};background:${soft}}blockquote p:last-child{margin-bottom:0}
    table{display:block;max-width:100%;overflow:auto;border-collapse:collapse;font-size:.88em}th,td{padding:10px 13px;border:1px solid ${line};text-align:left}th{background:${soft}}figure{margin-left:0;margin-right:0}figcaption{font-size:.8em;color:${muted};text-align:center}
    hr{border:0;border-top:1px solid ${line};margin:2em 0}.plain-text{white-space:pre-wrap}.content-empty{color:${muted}}.article-end{margin-top:55px;border-top:1px solid ${line};padding-top:20px;font-size:11px;color:${muted};letter-spacing:.5px}
    .image-error{padding:20px;border:1px dashed ${line};border-radius:6px;background:${soft};color:${muted};font-size:12px;text-align:center;margin:1.4em 0}.image-error a{margin-left:12px}
    @media(max-width:600px){.document{padding:28px 22px 64px}.article-title{font-size:26px}.article-meta{margin-bottom:24px;padding-bottom:22px}}
    </style></head><body><article class="document"><header><div class="article-feed">${escapeHTML(article.feedTitle || article.feedID || '阅读归档')}</div><h1 class="article-title">${escapeHTML(article.title || '无标题文章')}</h1><div class="article-meta">${metadata.map(text => `<span>${escapeHTML(text)}</span>`).join('')}</div></header>${article.source_deleted_at ? '<aside class="retained">历史保留 · 这篇文章已从原始库移除，仍可在此完整阅读。</aside>' : ''}<section class="article-content">${articleBody(article)}</section><footer class="article-end">已到文末 · NetNewsWire 阅读归档</footer></article><script data-reader-script nonce="${nonce}" src="${escapeHTML(helperURL)}"></script></body></html>`;
}

function updateReaderPreferences() {
  frame.contentWindow?.postMessage({type: 'nnw-reader-preferences', theme: document.documentElement.dataset.theme, fontSize}, '*');
  updateFontButtons();
}

window.addEventListener('message', event => {
  if (event.source === frame.contentWindow && event.data?.type === 'nnw-reader-ready') updateReaderPreferences();
});

function updateFontButtons() {
  els['font-smaller'].disabled = !state.article || fontSize <= 15;
  els['font-larger'].disabled = !state.article || fontSize >= 23;
}

function renderArticle() {
  if (!state.article) return;
  // Replace the browsing context so every article starts at the top, even on WebKit.
  const nextFrame = frame.cloneNode(false);
  nextFrame.srcdoc = readerDocument(state.article);
  nextFrame.hidden = false;
  frame.replaceWith(nextFrame);
  frame = nextFrame;
  els['reader-state'].hidden = true;
  els['reader-stage'].setAttribute('aria-busy', 'false');
  updateFontButtons();
}

async function loadArticle(articleID) {
  state.articleController?.abort();
  const controller = new AbortController();
  state.articleController = controller;
  const version = ++state.articleVersion;
  state.articleID = articleID;
  state.article = null;
  selectResult(articleID);
  els.workspace.classList.add('reading');
  els.original.hidden = true;
  els.original.removeAttribute('href');
  els['reader-source'].textContent = '正在打开文章…';
  showReaderState('正在打开文章', '正在读取归档中的正文…', {loading: true});
  if (narrowScreen.matches) els.back.focus({preventScroll: true});
  try {
    const article = await fetchJSON(`/api/articles/${encodeURIComponent(articleID)}`, controller.signal);
    if (version !== state.articleVersion) return;
    state.article = article;
    els['reader-source'].textContent = article.feedTitle || article.feedID || '阅读空间';
    const url = safeURL(article.url) || safeURL(article.externalURL);
    els.original.hidden = !url;
    if (url) els.original.href = url;
    renderArticle();
  } catch (error) {
    if (version !== state.articleVersion || error.name === 'AbortError') return;
    els['reader-source'].textContent = '文章加载失败';
    showReaderState('这篇文章暂时无法打开', `可以重试，或从列表选择另一篇文章。\n${error.message}`, {retry: true});
  }
}

function applyTheme() {
  const theme = themeChoice || (systemTheme.matches ? 'dark' : 'light');
  document.documentElement.dataset.theme = theme;
  const label = theme === 'dark' ? '切换浅色主题' : '切换深色主题';
  els.theme.setAttribute('aria-label', label);
  els.theme.title = label;
  els.theme.innerHTML = icon(theme === 'dark' ? 'sun' : 'moon');
  updateReaderPreferences();
}

els.filters.addEventListener('submit', event => { event.preventDefault(); search({resetPage: true}); });
for (const name of ['starred', 'unread']) els[name].addEventListener('click', () => { state[name] = !state[name]; search({resetPage: true}); });
els.all.addEventListener('click', () => { state.starred = false; state.unread = false; search({resetPage: true}); });
for (const name of ['feed', 'page-size', 'date-from', 'date-to']) els[name].addEventListener('change', () => search({resetPage: true}));
els.folder.addEventListener('change', () => { populateFeeds(); search({resetPage: true}); });
els.q.addEventListener('search', () => { if (!els.q.value) search({resetPage: true}); });
els.prev.addEventListener('click', () => { if (state.page > 1) { state.page -= 1; search(); } });
els.next.addEventListener('click', () => { if (state.page < state.pages) { state.page += 1; search(); } });
els.reset.addEventListener('click', resetFilters);
els['filter-toggle'].addEventListener('click', () => {
  const expanded = els['filter-panel'].hidden;
  els['filter-panel'].hidden = !expanded;
  els['filter-toggle'].setAttribute('aria-expanded', String(expanded));
  els['filter-toggle'].setAttribute('aria-label', expanded ? '收起筛选' : '展开筛选');
});
els['retry-article'].addEventListener('click', () => { if (state.articleID) loadArticle(state.articleID); });
function backToList() {
  els.workspace.classList.remove('reading');
  els.results.querySelector('.item.active')?.focus({preventScroll: true});
}
els.back.addEventListener('click', backToList);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && narrowScreen.matches) backToList();
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !['INPUT', 'SELECT', 'TEXTAREA'].includes(event.target.tagName)) {
    event.preventDefault(); backToList(); els.q.focus();
  }
});
els.theme.addEventListener('click', () => { themeChoice = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; preference('theme', themeChoice); applyTheme(); });
systemTheme.addEventListener('change', () => { if (!themeChoice) applyTheme(); });
for (const [name, delta] of [['font-smaller', -1], ['font-larger', 1]]) els[name].addEventListener('click', () => {
  fontSize = Math.max(15, Math.min(23, fontSize + delta));
  preference('font-size', fontSize);
  updateReaderPreferences();
});
applyTheme();
updateFontButtons();
loadStats();
loadFeeds();
search();
