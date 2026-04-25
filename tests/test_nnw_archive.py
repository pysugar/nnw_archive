from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "prototypes" / "localtools" / "rss" / "nnw_archive.py"
SPEC = importlib.util.spec_from_file_location("nnw_archive", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
nnw_archive = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nnw_archive
SPEC.loader.exec_module(nnw_archive)


SOURCE_SCHEMA = """
CREATE TABLE articles (
    articleID TEXT NOT NULL PRIMARY KEY,
    feedID TEXT NOT NULL,
    uniqueID TEXT NOT NULL,
    title TEXT,
    contentHTML TEXT,
    contentText TEXT,
    markdown TEXT,
    url TEXT,
    externalURL TEXT,
    summary TEXT,
    imageURL TEXT,
    bannerImageURL TEXT,
    datePublished DATE,
    dateModified DATE,
    searchRowID INTEGER
);
CREATE TABLE statuses (
    articleID TEXT NOT NULL PRIMARY KEY,
    read BOOL NOT NULL DEFAULT 0,
    starred BOOL NOT NULL DEFAULT 0,
    dateArrived DATE NOT NULL DEFAULT 0
);
CREATE TABLE authors (
    authorID TEXT NOT NULL PRIMARY KEY,
    name TEXT,
    url TEXT,
    avatarURL TEXT,
    emailAddress TEXT
);
CREATE TABLE authorsLookup (
    authorID TEXT NOT NULL,
    articleID TEXT NOT NULL,
    PRIMARY KEY(authorID, articleID)
);
"""


FEED_SETTINGS_SCHEMA = """
CREATE TABLE feedSettings (
    feedURL TEXT PRIMARY KEY,
    feedID TEXT NOT NULL DEFAULT '',
    homePageURL TEXT,
    iconURL TEXT,
    faviconURL TEXT,
    editedName TEXT,
    contentHash TEXT,
    newArticleNotificationsEnabled INTEGER NOT NULL DEFAULT 0,
    readerViewAlwaysEnabled INTEGER NOT NULL DEFAULT 0,
    authors TEXT,
    conditionalGetInfoLastModified TEXT,
    conditionalGetInfoEtag TEXT,
    conditionalGetInfoDate REAL,
    cacheControlInfoDateCreated REAL,
    cacheControlInfoMaxAge REAL,
    externalID TEXT,
    folderRelationship TEXT,
    lastCheckDate REAL
);
"""


def create_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SOURCE_SCHEMA)
        conn.execute(
            """
            INSERT INTO articles(
                articleID, feedID, uniqueID, title, contentHTML, contentText,
                markdown, url, externalURL, summary, imageURL, bannerImageURL,
                datePublished, dateModified, searchRowID
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "article-1",
                "feed-1",
                "unique-1",
                "Deep Search",
                "<p>Hello archive history</p>",
                None,
                None,
                "https://example.com/post",
                None,
                "A summary",
                None,
                None,
                1_700_000_000,
                None,
                7,
            ),
        )
        conn.execute(
            "INSERT INTO statuses(articleID, read, starred, dateArrived) VALUES(?, ?, ?, ?)",
            ("article-1", 0, 0, 1_700_000_100.0),
        )
        conn.execute(
            """
            INSERT INTO authors(authorID, name, url, avatarURL, emailAddress)
            VALUES(?, ?, ?, ?, ?)
            """,
            ("author-1", "Ada", "https://example.com/ada", None, "ada@example.com"),
        )
        conn.execute(
            "INSERT INTO authorsLookup(authorID, articleID) VALUES(?, ?)",
            ("author-1", "article-1"),
        )
        conn.commit()
    finally:
        conn.close()


def create_feed_settings_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(FEED_SETTINGS_SCHEMA)
        conn.execute(
            """
            INSERT INTO feedSettings(
                feedURL, feedID, homePageURL, iconURL, faviconURL, editedName,
                contentHash, externalID, lastCheckDate
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/feed.xml",
                "feed-1",
                "https://example.com/",
                "https://example.com/icon.png",
                "https://example.com/favicon.png",
                "Edited Feed",
                "hash",
                "external-feed-1",
                1_700_000_200.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def create_opml(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.1">
  <body>
    <outline text="Tech" title="Tech">
      <outline text="Example Feed" title="Example Feed" type="rss"
               htmlUrl="https://example.com/"
               xmlUrl="https://example.com/feed.xml"/>
    </outline>
  </body>
</opml>
""",
        encoding="utf-8",
    )


class NetNewsWireArchiveTests(unittest.TestCase):
    def make_fixture(self, tmpdir: str):
        root = Path(tmpdir)
        source = root / "DB.sqlite3"
        archive = root / "archive.sqlite3"
        feed_settings = root / "FeedSettings.db"
        opml = root / "Subscriptions.opml"
        create_source_db(source)
        create_feed_settings_db(feed_settings)
        create_opml(opml)
        return source, archive, feed_settings, opml

    def sync(self, source: Path, archive: Path, feed_settings: Path, opml: Path, now: str):
        return nnw_archive.sync_archive(
            source_db=source,
            archive_db=archive,
            feed_settings_db=feed_settings,
            opml_path=opml,
            feed_metadata_path=Path("/missing.plist"),
            now=now,
        )

    def request_handler(self, archive: Path, path: str, method: str = "GET"):
        handler_class = nnw_archive.make_handler(archive)
        handler = object.__new__(handler_class)
        handler.path = path
        handler.wfile = io.BytesIO()
        response: dict[str, object] = {"headers": []}

        def send_response(status: int) -> None:
            response["status"] = status

        def send_header(name: str, value: str) -> None:
            response["headers"].append((name, value))

        handler.send_response = send_response
        handler.send_header = send_header
        handler.end_headers = lambda: None
        if method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        response["body"] = handler.wfile.getvalue()
        return response

    def test_sync_inserts_articles_statuses_authors_and_feed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            result = self.sync(source, archive, feed_settings, opml, "2026-01-01T00:00:00Z")

            self.assertEqual(result.articles_inserted, 1)
            self.assertEqual(result.statuses_updated, 1)
            self.assertEqual(result.authors_upserted, 1)
            self.assertEqual(result.author_links_upserted, 1)
            self.assertEqual(result.feeds_updated, 1)

            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
                feed = conn.execute("SELECT * FROM archive_feeds WHERE feedID = 'feed-1'").fetchone()
                self.assertEqual(feed["display_name"], "Edited Feed")
                self.assertEqual(feed["folder_path"], "Tech")
                self.assertEqual(feed["feedURL"], "https://example.com/feed.xml")
            finally:
                conn.close()

    def test_sync_is_idempotent_but_keeps_seen_timestamp_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, feed_settings, opml, "2026-01-01T00:00:00Z")
            result = self.sync(source, archive, feed_settings, opml, "2026-01-02T00:00:00Z")

            self.assertEqual(result.articles_inserted, 0)
            self.assertEqual(result.articles_updated, 0)
            self.assertEqual(result.statuses_updated, 0)

            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                meta = conn.execute(
                    "SELECT created_at, updated_at, last_seen_at FROM archive_article_meta"
                ).fetchone()
                self.assertEqual(meta["created_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(meta["updated_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(meta["last_seen_at"], "2026-01-02T00:00:00Z")
            finally:
                conn.close()

    def test_latest_status_overwrites_archive_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, feed_settings, opml, "2026-01-01T00:00:00Z")

            conn = sqlite3.connect(source)
            try:
                conn.execute("UPDATE statuses SET read = 1, starred = 1 WHERE articleID = 'article-1'")
                conn.commit()
            finally:
                conn.close()

            self.sync(source, archive, feed_settings, opml, "2026-01-03T00:00:00Z")
            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                status = conn.execute("SELECT read, starred FROM statuses").fetchone()
                meta = conn.execute("SELECT created_at, updated_at FROM archive_article_meta").fetchone()
                self.assertEqual(status["read"], 1)
                self.assertEqual(status["starred"], 1)
                self.assertEqual(meta["created_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(meta["updated_at"], "2026-01-03T00:00:00Z")
            finally:
                conn.close()

    def test_deleted_source_article_is_retained_and_marked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, feed_settings, opml, "2026-01-01T00:00:00Z")

            conn = sqlite3.connect(source)
            try:
                conn.execute("DELETE FROM articles WHERE articleID = 'article-1'")
                conn.commit()
            finally:
                conn.close()

            result = self.sync(source, archive, feed_settings, opml, "2026-01-04T00:00:00Z")
            self.assertEqual(result.source_deleted_marked, 1)

            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
                deleted_at = conn.execute(
                    "SELECT source_deleted_at FROM archive_article_meta"
                ).fetchone()["source_deleted_at"]
                self.assertEqual(deleted_at, "2026-01-04T00:00:00Z")
            finally:
                conn.close()

    def test_search_and_web_api_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, feed_settings, opml, "2026-01-01T00:00:00Z")

            results = nnw_archive.search_articles(archive, q="archive history")
            self.assertEqual([row["articleID"] for row in results], ["article-1"])

            stats_response = self.request_handler(archive, "/api/stats")
            self.assertEqual(stats_response["status"], 200)
            stats = json.loads(stats_response["body"].decode("utf-8"))
            self.assertEqual(stats["articles"], 1)

            search_response = self.request_handler(archive, "/api/search?q=history")
            self.assertEqual(search_response["status"], 200)
            data = json.loads(search_response["body"].decode("utf-8"))
            self.assertEqual(data[0]["articleID"], "article-1")

            post_response = self.request_handler(archive, "/api/search", method="POST")
            self.assertEqual(post_response["status"], 405)


if __name__ == "__main__":
    unittest.main()
