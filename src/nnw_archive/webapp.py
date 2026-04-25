"""Flask web UI for the NetNewsWire archive."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request
from werkzeug.exceptions import HTTPException

from . import core


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NetNewsWire Archive</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f5f5f1;
      --panel: #fff;
      --ink: #1f2328;
      --muted: #667085;
      --line: #d8d9d2;
      --accent: #0f766e;
      --accent-soft: #d9f1ee;
      --warn: #9f1239;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #151719;
        --panel: #202327;
        --ink: #f2f4f5;
        --muted: #a5acb8;
        --line: #3b4148;
        --accent: #2dd4bf;
        --accent-soft: #143f3b;
        --warn: #fb7185;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 13px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .stats {
      margin-left: auto;
      color: var(--muted);
      white-space: nowrap;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(360px, 440px) 1fr;
      height: calc(100vh - 55px);
    }
    aside {
      min-width: 0;
      border-right: 1px solid var(--line);
      background: var(--panel);
      display: grid;
      grid-template-rows: auto auto 1fr auto;
    }
    .filters {
      display: grid;
      grid-template-columns: 1fr 130px 40px;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .secondary {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 0 12px 12px;
      border-bottom: 1px solid var(--line);
    }
    .toggles {
      display: flex;
      gap: 12px;
      align-items: center;
      color: var(--muted);
    }
    input, select, button {
      min-height: 34px;
      border: 1px solid var(--line);
      background: var(--bg);
      color: var(--ink);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      min-width: 0;
    }
    button {
      cursor: pointer;
      background: var(--accent);
      color: #fff;
      border-color: transparent;
    }
    button.secondary-button {
      background: transparent;
      color: var(--ink);
      border-color: var(--line);
    }
    .results {
      overflow: auto;
      min-height: 0;
    }
    .item {
      width: 100%;
      display: block;
      text-align: left;
      border: 0;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      color: var(--ink);
      background: transparent;
      padding: 12px;
    }
    .item:hover, .item.active { background: var(--accent-soft); }
    .item-title {
      font-weight: 620;
      overflow-wrap: anywhere;
    }
    .item-meta {
      color: var(--muted);
      margin-top: 5px;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .pager {
      display: grid;
      grid-template-columns: 40px 1fr 40px;
      gap: 8px;
      align-items: center;
      padding: 10px 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      background: var(--panel);
    }
    .pager button:disabled {
      opacity: .45;
      cursor: default;
    }
    main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .article-head {
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .article-head h2 {
      margin: 0 0 8px;
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .article-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      color: var(--muted);
    }
    iframe {
      width: 100%;
      height: 100%;
      border: 0;
      background: #fff;
    }
    .empty {
      padding: 24px;
      color: var(--muted);
    }
    .warn { color: var(--warn); }
    @media (max-width: 860px) {
      .layout {
        grid-template-columns: 1fr;
        height: auto;
        min-height: calc(100vh - 55px);
      }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .results { max-height: 42vh; }
      main { min-height: 58vh; }
      .stats { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>NetNewsWire Archive</h1>
    <div class="stats" id="stats"></div>
  </header>
  <div class="layout">
    <aside>
      <form class="filters" id="filters">
        <input id="q" name="q" placeholder="Search" autocomplete="off">
        <select id="feed" name="feed"><option value="">All feeds</option></select>
        <button type="submit">Go</button>
      </form>
      <div class="secondary">
        <select id="page-size">
          <option value="25">25 / page</option>
          <option value="50" selected>50 / page</option>
          <option value="100">100 / page</option>
          <option value="200">200 / page</option>
        </select>
        <div class="toggles">
          <label><input id="starred" type="checkbox"> Starred</label>
          <label><input id="unread" type="checkbox"> Unread</label>
        </div>
      </div>
      <div class="results" id="results"></div>
      <div class="pager">
        <button id="prev" class="secondary-button" type="button">‹</button>
        <div id="page-label"></div>
        <button id="next" class="secondary-button" type="button">›</button>
      </div>
    </aside>
    <main>
      <section class="article-head">
        <h2 id="article-title">Select an article</h2>
        <div class="article-meta" id="article-meta"></div>
      </section>
      <iframe id="article-frame" sandbox="allow-popups allow-popups-to-escape-sandbox"></iframe>
    </main>
  </div>
  <script>
    const els = {
      stats: document.getElementById('stats'),
      filters: document.getElementById('filters'),
      q: document.getElementById('q'),
      feed: document.getElementById('feed'),
      starred: document.getElementById('starred'),
      unread: document.getElementById('unread'),
      pageSize: document.getElementById('page-size'),
      results: document.getElementById('results'),
      prev: document.getElementById('prev'),
      next: document.getElementById('next'),
      pageLabel: document.getElementById('page-label'),
      title: document.getElementById('article-title'),
      meta: document.getElementById('article-meta'),
      frame: document.getElementById('article-frame'),
    };
    let page = 1;
    let pages = 1;

    function fmtTime(value) {
      if (value === null || value === undefined || value === '') return '';
      const num = Number(value);
      if (!Number.isNaN(num)) return new Date(num * 1000).toLocaleDateString();
      return String(value);
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }

    function queryParams() {
      const params = new URLSearchParams();
      if (els.q.value) params.set('q', els.q.value);
      if (els.feed.value) params.set('feed_id', els.feed.value);
      if (els.starred.checked) params.set('starred', '1');
      if (els.unread.checked) params.set('unread', '1');
      params.set('page', String(page));
      params.set('page_size', els.pageSize.value);
      return params;
    }

    async function loadStats() {
      const data = await fetch('/api/stats').then(r => r.json());
      els.stats.textContent = `${data.articles} articles | ${data.feeds} feeds | ${data.source_deleted} retained`;
    }

    async function loadFeeds() {
      const data = await fetch('/api/feeds').then(r => r.json());
      for (const item of data) {
        const option = document.createElement('option');
        option.value = item.feedID;
        option.textContent = `${item.display_name || item.feedID} (${item.article_count})`;
        els.feed.appendChild(option);
      }
    }

    function renderResults(payload) {
      els.results.replaceChildren();
      pages = payload.pages || 1;
      els.pageLabel.textContent = `Page ${payload.page} / ${pages} · ${payload.total} results`;
      els.prev.disabled = payload.page <= 1;
      els.next.disabled = payload.page >= pages;
      if (!payload.items.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'No results';
        els.results.appendChild(empty);
        return;
      }
      for (const item of payload.items) {
        const button = document.createElement('button');
        button.className = 'item';
        button.type = 'button';
        const itemTitle = document.createElement('div');
        itemTitle.className = 'item-title';
        itemTitle.textContent = item.title || '(untitled)';
        const itemMeta = document.createElement('div');
        itemMeta.className = 'item-meta';
        itemMeta.textContent = [
          item.feedTitle || item.feedID,
          fmtTime(item.datePublished || item.dateArrived),
          item.starred ? 'starred' : '',
          item.source_deleted_at ? 'retained' : ''
        ].filter(Boolean).join(' | ');
        button.append(itemTitle, itemMeta);
        button.addEventListener('click', () => loadArticle(item.articleID, button));
        els.results.appendChild(button);
      }
    }

    async function search() {
      const payload = await fetch(`/api/search?${queryParams()}`).then(r => r.json());
      renderResults(payload);
    }

    async function loadArticle(articleID, button) {
      document.querySelectorAll('.item.active').forEach(el => el.classList.remove('active'));
      button?.classList.add('active');
      const article = await fetch(`/api/articles/${encodeURIComponent(articleID)}`).then(r => r.json());
      els.title.textContent = article.title || '(untitled)';
      els.meta.replaceChildren();
      for (const value of [
        article.feedTitle || article.feedID,
        fmtTime(article.datePublished || article.dateArrived),
        article.starred ? 'starred' : '',
        article.source_deleted_at ? 'retained from source deletion' : ''
      ].filter(Boolean)) {
        const span = document.createElement('span');
        span.textContent = value;
        if (value.startsWith('retained')) span.className = 'warn';
        els.meta.appendChild(span);
      }
      const body = article.contentHTML || `<pre>${escapeHtml(article.contentText || article.markdown || article.summary || '')}</pre>`;
      els.frame.srcdoc = `<!doctype html><meta charset="utf-8"><base target="_blank"><style>body{font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:28px;max-width:820px;color:#202124}img{max-width:100%;height:auto}pre{white-space:pre-wrap}</style>${body}`;
    }

    els.filters.addEventListener('submit', event => { event.preventDefault(); page = 1; search(); });
    els.starred.addEventListener('change', () => { page = 1; search(); });
    els.unread.addEventListener('change', () => { page = 1; search(); });
    els.feed.addEventListener('change', () => { page = 1; search(); });
    els.pageSize.addEventListener('change', () => { page = 1; search(); });
    els.prev.addEventListener('click', () => { if (page > 1) { page -= 1; search(); } });
    els.next.addEventListener('click', () => { if (page < pages) { page += 1; search(); } });
    loadStats();
    loadFeeds().then(search);
  </script>
</body>
</html>
"""


def int_arg(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def bool_arg(name: str) -> bool | None:
    value = request.args.get(name)
    return core.bool_from_param(value)


def create_app(archive_db: Path) -> Flask:
    app = Flask(__name__)
    app.config["ARCHIVE_DB"] = archive_db

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        core.logger.exception("Web request failed")
        return jsonify({"error": str(exc)}), 500

    @app.route("/")
    def index():
        return render_template_string(INDEX_HTML)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(core.get_stats(app.config["ARCHIVE_DB"]))

    @app.route("/api/feeds")
    def api_feeds():
        return jsonify(core.list_feeds(app.config["ARCHIVE_DB"]))

    @app.route("/api/search")
    def api_search():
        result = core.search_articles_page(
            app.config["ARCHIVE_DB"],
            q=request.args.get("q", ""),
            feed_id=request.args.get("feed_id") or None,
            folder=request.args.get("folder") or None,
            starred=bool_arg("starred"),
            unread=bool_arg("unread"),
            date_from=request.args.get("from") or None,
            date_to=request.args.get("to") or None,
            page=int_arg("page", 1, minimum=1, maximum=100000),
            page_size=int_arg("page_size", 50, minimum=10, maximum=200),
        )
        return jsonify(result)

    @app.route("/api/articles/<path:article_id>")
    def api_article(article_id: str):
        article = core.get_article(app.config["ARCHIVE_DB"], article_id)
        if article is None:
            return jsonify({"error": "article not found"}), 404
        return jsonify(article)

    return app


def run_server(archive_db: Path, *, host: str = "127.0.0.1", port: int = 8765, debug: bool = False) -> None:
    conn = core.connect_archive(archive_db)
    try:
        core.ensure_archive_schema(conn)
    finally:
        conn.close()

    app = create_app(archive_db)
    app.run(host=host, port=port, debug=debug)
