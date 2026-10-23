"""Exercise the archive UI against a disposable fixture, without accessing live data."""

from __future__ import annotations

import argparse
import base64
import json
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import request
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from tests.test_nnw_archive import (
    create_feed_settings_db,
    create_opml,
    create_source_db,
    nnw_archive as core,
)
from nnw_archive.webapp import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def create_fixture(root: Path) -> Path:
    source = root / "DB.sqlite3"
    archive = root / "archive.sqlite3"
    create_source_db(source)
    create_feed_settings_db(root / "FeedSettings.db")
    create_opml(root / "Subscriptions.opml")
    titles = [
        "为长期阅读，建立一个安静的信息空间",
        "Small tools, lasting ideas: a personal knowledge archive",
        "从订阅到沉淀：让信息成为自己的知识",
        "设计中的留白，是为了让内容被看见",
        "The web we want to keep",
        "重读的价值：在旧文章里发现新的连接",
    ]
    paragraph = "信息每天都在更新，但真正有价值的内容，值得被长久保存。一个好的阅读空间，不只是收藏文章的地方，也是让想法慢慢生长的地方。"
    rich_body = f"""
      <p>我们常常花很多时间寻找新内容，却很少回到那些曾经改变自己想法的文章。</p>
      <h2>让阅读回到内容本身</h2><p>{paragraph}</p>
      <blockquote><p>保存不是终点。能够轻松地找回、重读和理解，才是归档的意义。</p></blockquote>
      <p>从 <a href="../notes">一篇笔记</a> 开始，给未来的自己留下一条清晰的线索。</p>
      <h2>简单、可靠，也足够耐心</h2><ul><li>内容始终在眼前，不被界面打断。</li><li>每一次切换，都从文章开头开始。</li><li>保留上下文，也保留重新发现的可能。</li></ul>
      <pre><code>archive.search(query="ideas", page=1)</code></pre>
      <table><thead><tr><th>习惯</th><th>价值</th></tr></thead><tbody><tr><td>持续阅读</td><td>建立联系</td></tr><tr><td>定期重读</td><td>发现变化</td></tr></tbody></table>
    """
    unsafe = """<script>top.__feedScriptRan = true;</script><style>body{display:none}</style>
      <meta http-equiv="refresh" content="0;url=https://example.com/redirect">
      <iframe src="https://example.com/embed"></iframe><p onclick="alert(1)">安全正文</p>
      <a href="javascript:alert(1)">不安全链接</a>"""
    with closing(sqlite3.connect(source)) as conn, conn:
        for i in range(1, 86):
            body = rich_body + ("<p>" + paragraph + "</p>") * (65 if i % 2 else 2)
            if i == 85:
                body += unsafe + """
                  <img alt="primary fallback" src="https://images.example.test/blocked.png" data-src="https://images.example.test/fallback.png">
                  <img alt="lazy only" data-src="../images/lazy.png">
                  <picture><source srcset="https://images.example.test/small.png 400w, https://images.example.test/w_1200,c_limit/large.png 1200w"><img alt="responsive only"></picture>
                  <img alt="permanently unavailable" src="https://images.example.test/missing.png">
                """
                body = "<![CDATA[" + body + "]]>"
            conn.execute(
                "INSERT INTO articles(articleID, feedID, uniqueID, title, contentHTML, summary, url, datePublished) VALUES(?,?,?,?,?,?,?,?)",
                (f"story-{i:03}", "feed-1", f"story-{i:03}", titles[(i - 1) % len(titles)], body,
                 "一个好的阅读空间，不只是收藏文章的地方，也是让想法慢慢生长的地方。", f"https://example.com/posts/{i}", 1_760_000_000 + i * 86400),
            )
            conn.execute("INSERT INTO statuses VALUES(?,?,?,?)", (f"story-{i:03}", i % 2, int(i % 5 == 0), 1_760_000_000 + i * 86400))
    core.sync_archive(source_db=source, archive_db=archive, feed_metadata_path=root / "missing.plist")
    with closing(sqlite3.connect(archive)) as conn, conn:
        conn.execute("UPDATE archive_feeds SET display_name = 'The Reading Room', folder_path = '阅读与设计'")
    return archive


def run_checks(base_url: str, output: Path, channel: str) -> list[str]:
    checks: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=channel or None)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
        # External feed resources must not contact real websites during a regression run.
        def route_request(route):
            url = urlparse(route.request.url)
            if url.hostname == "127.0.0.1":
                route.continue_()
            elif url.path.endswith(("fallback.png", "lazy.png", "large.png")):
                route.fulfill(content_type="image/png", body=base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="))
            else:
                route.fulfill(status=403, body="Forbidden")

        context.route("**/*", route_request)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")
        expect(page.locator(".item")).to_have_count(50)
        expect(page.locator("#result-count")).to_have_text("86 篇文章")
        page.screenshot(path=str(output / "desktop-empty.png"))

        def open_article(article_id: str):
            page.locator(f'.item[data-article-id="{article_id}"]').click()
            expect(page.frame_locator("#article-frame").locator(".article-title")).to_be_visible()

        def reader_frame():
            return page.locator("#article-frame").element_handle().content_frame()

        def assert_viewport():
            dimensions = page.evaluate("""() => ({
              height: innerHeight, width: innerWidth,
              docHeight: document.documentElement.scrollHeight, docWidth: document.documentElement.scrollWidth,
              windowScroll: scrollY,
              readerTop: document.querySelector('#article-frame').getBoundingClientRect().top,
              readerBottom: document.querySelector('#article-frame').getBoundingClientRect().bottom,
            })""")
            assert dimensions["docHeight"] <= dimensions["height"] + 1, dimensions
            assert dimensions["docWidth"] <= dimensions["width"], dimensions
            assert dimensions["windowScroll"] == 0, dimensions
            assert 0 <= dimensions["readerTop"] < dimensions["height"], dimensions
            assert dimensions["readerBottom"] <= dimensions["height"] + 1, dimensions

        open_article("story-085")
        reader = reader_frame()
        assert reader.locator(".article-content script, iframe, meta[http-equiv=refresh], [onclick]").count() == 0
        assert reader.locator('a[href^="javascript:"]').count() == 0
        expect(reader.get_by_role("link", name="一篇笔记")).to_have_attribute("href", "https://example.com/notes")
        assert not page.evaluate("Boolean(window.__feedScriptRan)")
        assert reader.evaluate("() => { try { return parent.location.href; } catch { return 'blocked'; } }") == "blocked"
        reader.evaluate("() => { const script = document.createElement('script'); script.textContent = 'window.untrustedScriptRan = true'; document.body.append(script); }")
        assert not reader.evaluate("Boolean(window.untrustedScriptRan)")
        checks.append("feed markup isolation and relative URLs")
        page.screenshot(path=str(output / "desktop-article.png"))

        reader.locator("img").evaluate_all("images => images.forEach(image => image.loading = 'eager')")
        for label in ["primary fallback", "lazy only", "responsive only"]:
            expect(reader.get_by_alt_text(label)).to_have_js_property("naturalWidth", 1)
        expect(reader.get_by_alt_text("primary fallback")).to_have_attribute("src", "https://images.example.test/fallback.png")
        expect(reader.get_by_alt_text("lazy only")).to_have_attribute("src", "https://example.com/images/lazy.png")
        expect(reader.get_by_alt_text("responsive only")).to_have_attribute("src", "https://images.example.test/w_1200,c_limit/large.png")
        expect(reader.locator(".image-error")).to_be_visible()
        expect(reader.get_by_alt_text("permanently unavailable")).to_be_hidden()
        expect(reader.locator(".article-content > p").first).to_contain_text("我们常常花很多时间")
        checks.append("CDATA, image fallback after 403, lazy/srcset-only images and failed-image message")

        reader.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        assert reader.evaluate("scrollY") > 1500
        page.locator("#results").evaluate("element => element.scrollTop = element.scrollHeight")
        list_scroll = page.locator("#results").evaluate("element => element.scrollTop")
        assert list_scroll > 1500
        open_article("story-036")
        assert reader_frame().evaluate("scrollY") == 0
        assert page.locator("#results").evaluate("element => element.scrollTop") >= list_scroll - 1
        assert_viewport()
        checks.append("list bottom + long-to-short article resets reader without moving list or page")

        reader_frame().evaluate("window.scrollTo(0, 200)")
        page.get_by_role("button", name="放大正文字号").click()
        expect(page.frame_locator("#article-frame").locator("body")).to_have_css("font-size", "18px")
        assert reader_frame().evaluate("scrollY") > 0
        page.get_by_role("button", name="切换深色主题").click()
        expect(page.frame_locator("#article-frame").locator("html")).to_have_css("background-color", "rgb(28, 37, 34)")
        assert reader_frame().evaluate("scrollY") > 0
        reader_frame().evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(output / "desktop-dark.png"))
        page.get_by_role("button", name="切换浅色主题").click()
        checks.append("font size and synchronized dark theme preserve reading position")

        page.locator("#next").click()
        expect(page.locator("#page-label")).to_have_text("2 / 2")
        expect(page.locator(".item")).to_have_count(36)
        assert page.locator("#results").evaluate("element => element.scrollTop") == 0
        expect(page.locator("#article-frame")).to_be_hidden()
        page.locator("#starred").click()
        expect(page.locator("#page-label")).to_have_text("1 / 1")
        expect(page.locator("#result-count")).to_have_text("17 篇文章")
        page.locator("#unread").click()
        expect(page.locator("#result-count")).to_have_text("8 篇文章")
        page.locator("#reset").click()
        expect(page.locator(".item")).to_have_count(50)
        checks.append("pagination, combined status filters and selection clearing")

        page.locator("#filter-toggle").click()
        page.locator("#folder").select_option("阅读与设计")
        expect(page.locator("#filter-count")).to_have_text("1")
        expect(page.locator("#result-count")).to_have_text("86 篇文章")
        page.locator("#q").fill("no-such-result-zzzz")
        page.locator("#search-submit").click()
        expect(page.get_by_text("这里还没有文章")).to_be_visible()
        expect(page.locator("#next")).to_be_disabled()
        page.locator("#reset").click()
        expect(page.locator(".item")).to_have_count(50)
        page.locator("#filter-toggle").click()
        checks.append("folder filter and recoverable empty search")

        page.route("**/api/search?**", lambda route: route.fulfill(status=500, json={"error": "Simulated failure"}))
        page.locator("#search-submit").click()
        expect(page.get_by_text("暂时无法加载文章")).to_be_visible()
        page.unroute("**/api/search?**")
        page.get_by_role("button", name="重试", exact=True).click()
        expect(page.locator(".item")).to_have_count(50)
        page.route("**/api/articles/story-085", lambda route: route.fulfill(status=404, json={"error": "Missing article"}))
        page.locator('.item[data-article-id="story-085"]').click()
        expect(page.get_by_text("这篇文章暂时无法打开")).to_be_visible()
        expect(page.locator("#original")).to_be_hidden()
        page.unroute("**/api/articles/story-085")
        page.get_by_role("button", name="重新加载").click()
        expect(page.frame_locator("#article-frame").locator(".article-title")).to_be_visible()
        checks.append("search and article failures can be retried")

        # Ignore aborts deliberately to verify the stale-response guards themselves.
        page.evaluate("""() => {
          const original = window.fetch;
          window.fetch = (url, options = {}) => original(url, {...options, signal: undefined});
        }""")
        page.locator("#q").fill("history")
        with page.expect_request("**/api/search?*q=history*"):
            page.locator("#search-submit").click()
        page.locator("#q").fill("lasting")
        page.locator("#search-submit").click()
        expect(page.locator("#result-count")).to_have_text("14 篇文章")
        page.wait_for_timeout(700)
        expect(page.locator("#result-count")).to_have_text("14 篇文章")
        page.locator("#reset").click()
        expect(page.locator(".item")).to_have_count(50)
        page.locator('.item[data-article-id="story-083"]').click()
        page.locator('.item[data-article-id="story-084"]').click()
        expect(page.locator("#original")).to_have_attribute("href", "https://example.com/posts/84")
        page.wait_for_timeout(700)
        expect(page.locator("#original")).to_have_attribute("href", "https://example.com/posts/84")
        checks.append("out-of-order search and article responses cannot replace latest selection")

        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(".library")).to_be_hidden()
        assert_viewport()
        page.get_by_role("button", name="返回文章列表").click()
        expect(page.locator(".library")).to_be_visible()
        page.locator("#results").evaluate("element => element.scrollTop = element.scrollHeight")
        list_scroll = page.locator("#results").evaluate("element => element.scrollTop")
        open_article("story-036")
        assert_viewport()
        page.screenshot(path=str(output / "mobile-article.png"))
        page.get_by_role("button", name="返回文章列表").click()
        assert abs(page.locator("#results").evaluate("element => element.scrollTop") - list_scroll) < 2
        page.screenshot(path=str(output / "mobile-list.png"))
        page.set_viewport_size({"width": 320, "height": 568})
        page.locator("#filter-toggle").click()
        expect(page.locator("#date-to")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        checks.append("mobile reader, return-to-list scroll preservation and 320px filters")
        assert not errors, errors
        checks.append("no uncaught browser errors")
        browser.close()
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/nnw-archive-browser-check"))
    parser.add_argument("--channel", default="chrome", help="Installed Playwright browser channel; use an empty string for bundled Chromium.")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nnw-browser-") as tmpdir:
        archive = create_fixture(Path(tmpdir))
        app = create_app(archive)

        @app.before_request
        def delay_old_requests():
            if request.args.get("q") == "history" or request.path.endswith("story-083"):
                time.sleep(.5)

        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            checks = run_checks(f"http://127.0.0.1:{server.server_port}", args.output_dir, args.channel)
            payload = {"status": "PASS", "checks": checks}
            (args.output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
