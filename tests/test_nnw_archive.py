from __future__ import annotations

import json
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from nnw_archive import cli as nnw_cli
from nnw_archive import core as nnw_archive
from nnw_archive.webapp import create_app


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


MODERN_SOURCE_SCHEMA = """
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
    searchRowID INTEGER,
    authors TEXT
);
CREATE TABLE statuses (
    articleID TEXT NOT NULL PRIMARY KEY,
    read BOOL NOT NULL DEFAULT 0,
    starred BOOL NOT NULL DEFAULT 0,
    dateArrived DATE NOT NULL DEFAULT 0
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


def create_modern_source_db(path: Path, authors: str | None) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(MODERN_SOURCE_SCHEMA)
        conn.execute(
            """
            INSERT INTO articles(
                articleID, feedID, uniqueID, title, contentHTML, contentText,
                markdown, url, externalURL, summary, imageURL, bannerImageURL,
                datePublished, dateModified, searchRowID, authors
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                authors,
            ),
        )
        conn.execute(
            "INSERT INTO statuses(articleID, read, starred, dateArrived) VALUES(?, ?, ?, ?)",
            ("article-1", 0, 0, 1_700_000_100.0),
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

    def make_modern_fixture(self, tmpdir: str, authors: str | None):
        root = Path(tmpdir)
        source = root / "DB.sqlite3"
        archive = root / "archive.sqlite3"
        feed_settings = root / "FeedSettings.db"
        opml = root / "Subscriptions.opml"
        create_modern_source_db(source, authors)
        create_feed_settings_db(feed_settings)
        create_opml(opml)
        return source, archive, feed_settings, opml

    def sync(
        self,
        source: Path,
        archive: Path,
        feed_settings: Path,
        opml: Path,
        now: str,
        *,
        allow_schema_change: bool = False,
    ):
        return nnw_archive.sync_archive(
            source_db=source,
            archive_db=archive,
            feed_settings_db=feed_settings,
            opml_path=opml,
            feed_metadata_path=Path("/missing.plist"),
            allow_schema_change=allow_schema_change,
            now=now,
        )

    def test_sync_inserts_articles_statuses_authors_and_feed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            result = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

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

    def test_sync_reads_modern_article_authors_json(self) -> None:
        authors = json.dumps(
            [
                {
                    "authorID": "author-1",
                    "name": "Ada",
                    "url": "https://example.com/ada",
                    "avatarURL": "https://example.com/ada.png",
                },
                {
                    "name": "Grace",
                    "url": "https://example.com/grace",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_modern_fixture(tmpdir, authors)
            result = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(result.articles_inserted, 1)
            self.assertEqual(result.authors_upserted, 2)
            self.assertEqual(result.author_links_upserted, 2)

            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM authors ORDER BY name").fetchall()
                self.assertEqual([row["name"] for row in rows], ["Ada", "Grace"])
                self.assertIsNone(rows[0]["emailAddress"])
                self.assertTrue(rows[1]["authorID"].startswith("generated:"))
                links = conn.execute("SELECT COUNT(*) FROM authorsLookup").fetchone()[0]
                self.assertEqual(links, 2)
            finally:
                conn.close()

    def test_sync_allows_modern_source_without_author_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_modern_fixture(tmpdir, None)
            result = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(result.articles_inserted, 1)
            self.assertEqual(result.authors_seen, 0)
            self.assertEqual(result.author_links_seen, 0)

    def test_sync_skips_invalid_modern_article_authors_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_modern_fixture(
                tmpdir, "[not valid json"
            )
            result = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            self.assertEqual(result.articles_inserted, 1)
            self.assertEqual(result.authors_seen, 0)
            self.assertEqual(result.author_links_seen, 0)

            conn = sqlite3.connect(archive)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0], 0)
            finally:
                conn.close()

    def test_sync_records_source_schema_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            result = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            conn = sqlite3.connect(archive)
            conn.row_factory = sqlite3.Row
            try:
                metadata_payload = conn.execute(
                    "SELECT value FROM archive_metadata WHERE key = 'source_schema_payload'"
                ).fetchone()["value"]
                schema = conn.execute(
                    "SELECT * FROM archive_source_schemas WHERE schema_hash = ?",
                    (result.source_schema_hash,),
                ).fetchone()

                self.assertIn(
                    "table|articles|articles|CREATE TABLE articles",
                    metadata_payload,
                )
                self.assertEqual(schema["payload"], metadata_payload)
                self.assertEqual(schema["first_seen_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(schema["last_seen_at"], "2026-01-01T00:00:00Z")
            finally:
                conn.close()

    def test_schema_hash_change_reports_diff_and_requires_allow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            first = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            conn = sqlite3.connect(source)
            try:
                conn.execute("CREATE INDEX articles_title_idx ON articles(title)")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaises(RuntimeError) as ctx:
                self.sync(source, archive, feed_settings, opml, "2026-01-02T00:00:00Z")

            message = str(ctx.exception)
            self.assertIn("Source schema hash changed.", message)
            self.assertIn(f"Previous schema hash: {first.source_schema_hash}", message)
            self.assertIn("Schema change category: auxiliary schema objects changed", message)
            self.assertIn("+index|articles_title_idx|articles|CREATE INDEX", message)

            accepted = self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-02T00:00:00Z",
                allow_schema_change=True,
            )
            self.assertNotEqual(accepted.source_schema_hash, first.source_schema_hash)

            conn = sqlite3.connect(archive)
            try:
                current_hash = conn.execute(
                    "SELECT value FROM archive_metadata WHERE key = 'source_schema_hash'"
                ).fetchone()[0]
                schema_count = conn.execute(
                    "SELECT COUNT(*) FROM archive_source_schemas"
                ).fetchone()[0]
                self.assertEqual(current_hash, accepted.source_schema_hash)
                self.assertEqual(schema_count, 2)
            finally:
                conn.close()

    def test_schema_hash_change_classifies_core_table_definition_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            self.sync(
                source,
                archive,
                feed_settings,
                opml,
                "2026-01-01T00:00:00Z",
            )

            conn = sqlite3.connect(source)
            try:
                conn.execute("ALTER TABLE articles ADD COLUMN readingTime INTEGER")
                conn.commit()
            finally:
                conn.close()

            with self.assertRaises(RuntimeError) as ctx:
                self.sync(source, archive, feed_settings, opml, "2026-01-02T00:00:00Z")

            self.assertIn(
                "Schema change category: core table definitions changed",
                str(ctx.exception),
            )

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

            app = create_app(archive)
            client = app.test_client()

            stats_response = client.get("/api/stats")
            self.assertEqual(stats_response.status_code, 200)
            stats = stats_response.get_json()
            self.assertEqual(stats["articles"], 1)

            search_response = client.get("/api/search?q=history&page=1&page_size=10")
            self.assertEqual(search_response.status_code, 200)
            data = search_response.get_json()
            self.assertEqual(data["page"], 1)
            self.assertEqual(data["page_size"], 10)
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["articleID"], "article-1")

            post_response = client.post("/api/search")
            self.assertEqual(post_response.status_code, 405)

    def test_click_stats_and_default_source_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, archive, feed_settings, opml = self.make_fixture(tmpdir)
            fallback = root / "netnewswire.sqlite3"
            source.rename(fallback)

            resolved = nnw_archive.resolve_source_db(
                cwd=root, preferred_source=root / "missing-native.sqlite3"
            )
            self.assertEqual(resolved, fallback.resolve())

            self.sync(fallback, archive, feed_settings, opml, "2026-01-01T00:00:00Z")

            runner = CliRunner()
            result = runner.invoke(
                nnw_cli.cli,
                ["stats", "--archive-db", str(archive), "--data-dir", str(root), "--json"],
            )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            payload = json.loads(result.output)
            self.assertEqual(payload["articles"], 1)

    def test_read_apis_use_readonly_connections_and_do_not_initialize_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            client = create_app(archive).test_client()
            before = archive.read_bytes()
            with patch.object(nnw_archive, "connect_archive", side_effect=AssertionError("Write connection opened")):
                for url in ["/api/stats", "/api/feeds", "/api/search?q=history", "/api/articles/article-1"]:
                    with self.subTest(url=url):
                        self.assertEqual(client.get(url).status_code, 200)
            self.assertEqual(archive.read_bytes(), before)
            conn = nnw_archive.connect_readonly(archive)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE statuses SET read = 1")
            finally:
                conn.close()

    def test_stats_ignore_orphan_statuses_and_match_unread_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            with closing(sqlite3.connect(archive)) as conn, conn:
                conn.execute("INSERT INTO statuses VALUES('missing', 0, 1, 0)")
                conn.execute("DELETE FROM statuses WHERE articleID = 'article-1'")
            stats = nnw_archive.get_stats(archive)
            self.assertEqual(stats["starred"], 0)
            self.assertEqual(stats["unread"], 1)
            self.assertEqual(stats["unread"], nnw_archive.count_articles(archive, unread=True))

    def test_date_only_upper_bound_includes_whole_day_but_not_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            client = create_app(archive).test_client()
            # The fixture is published at 2023-11-14 22:13:20 UTC.
            data = client.get("/api/search?from=2023-11-14&to=2023-11-14").get_json()
            self.assertEqual(data["total"], 1)
            self.assertEqual(client.get("/api/search?to=2023-11-13").get_json()["total"], 0)
            self.assertEqual(client.get("/api/search?to=1700000000").get_json()["total"], 1)
            self.assertEqual(client.get("/api/search?to=2023-11-14T00:00:00Z").get_json()["total"], 0)
            with closing(sqlite3.connect(archive)) as conn, conn:
                conn.execute("UPDATE articles SET datePublished = ?", (nnw_archive.parse_date_filter("2023-11-15"),))
            self.assertEqual(client.get("/api/search?to=2023-11-14").get_json()["total"], 0)

    def test_invalid_date_filters_return_json_400(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            client = create_app(archive).test_client()
            for query in ["from=invalid", "to=nan", "to=inf", "to=2026-02-30", "from=2026-01-02&to=2026-01-01"]:
                with self.subTest(query=query):
                    response = client.get(f"/api/search?{query}")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())

    def test_pagination_clamps_out_of_range_page_and_handles_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            client = create_app(archive).test_client()
            payload = client.get("/api/search?page=1000&page_size=10").get_json()
            self.assertEqual((payload["page"], payload["pages"], len(payload["items"])), (1, 1, 1))
            payload = client.get("/api/search?page=1000&q=nonexistent").get_json()
            self.assertEqual((payload["page"], payload["pages"], payload["items"]), (1, 1, []))

    def test_web_template_assets_and_missing_article(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            client = create_app(archive).test_client()
            self.assertIn(b'lang="zh-CN"', client.get("/").data)
            for path in ["/static/app.js", "/static/style.css"]:
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                response.close()
            self.assertEqual(client.get("/api/articles/missing").status_code, 404)
            self.assertIn("error", client.get("/api/articles/missing").get_json())

    def test_search_excerpt_strips_markup_and_falls_back_to_html_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            with closing(sqlite3.connect(archive)) as conn, conn:
                conn.execute("UPDATE articles SET summary = ''")
            article = nnw_archive.search_articles(archive)[0]
            self.assertEqual(article["excerpt"], "Hello archive history")
            self.assertNotIn("preview_source", article)

    def test_sync_refuses_source_as_archive_including_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, _, settings, opml = self.make_fixture(tmpdir)
            alias = Path(tmpdir) / "alias.sqlite3"
            alias.hardlink_to(source)
            before = source.read_bytes()
            for archive in [source, alias]:
                with self.subTest(archive=archive), self.assertRaisesRegex(ValueError, "must be different"):
                    self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            self.assertEqual(source.read_bytes(), before)

    def test_malformed_opml_does_not_abort_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_fixture(tmpdir)
            opml.write_text("<opml/>", encoding="utf-8")
            result = self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            self.assertEqual(result.articles_inserted, 1)

    def test_author_link_replacement_preserves_retained_article_authors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_modern_fixture(tmpdir, json.dumps([{"name": "Ada"}]))
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("UPDATE articles SET authors = ?", (json.dumps([{"name": "Grace"}]),))
            self.sync(source, archive, settings, opml, "2026-01-02T00:00:00Z")
            self.assertEqual([a["name"] for a in nnw_archive.get_article(archive, "article-1")["authors"]], ["Grace"])
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("DELETE FROM articles")
            self.sync(source, archive, settings, opml, "2026-01-03T00:00:00Z")
            self.assertEqual([a["name"] for a in nnw_archive.get_article(archive, "article-1")["authors"]], ["Grace"])

    def test_invalid_author_json_keeps_existing_links_but_empty_list_clears_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source, archive, settings, opml = self.make_modern_fixture(tmpdir, json.dumps([{"name": "Ada"}]))
            self.sync(source, archive, settings, opml, "2026-01-01T00:00:00Z")
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("UPDATE articles SET authors = '[invalid'")
            self.sync(source, archive, settings, opml, "2026-01-02T00:00:00Z")
            self.assertEqual(len(nnw_archive.get_article(archive, "article-1")["authors"]), 1)
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("UPDATE articles SET authors = '[]'")
            self.sync(source, archive, settings, opml, "2026-01-03T00:00:00Z")
            self.assertEqual(nnw_archive.get_article(archive, "article-1")["authors"], [])

    def test_launchd_keeps_metadata_overrides_and_rejects_invalid_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = CliRunner()
            result = runner.invoke(nnw_cli.cli, [
                "install-launchd", "--dry-run", "--data-dir", str(root),
                "--feed-settings-db", str(root / "settings.db"),
                "--opml", str(root / "feeds.opml"), "--feed-metadata", str(root / "metadata.plist"),
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            arguments = plistlib.loads(result.stdout_bytes)["ProgramArguments"]
            self.assertEqual(arguments[:3], [sys.executable, str(Path(nnw_cli.__file__).resolve()), "sync"])
            for flag, filename in [("--feed-settings-db", "settings.db"), ("--opml", "feeds.opml"), ("--feed-metadata", "metadata.plist")]:
                self.assertEqual(arguments[arguments.index(flag) + 1], str((root / filename).resolve()))
            for flag, value in [("--hour", "24"), ("--minute", "60"), ("--hour", "-1")]:
                result = runner.invoke(nnw_cli.cli, ["install-launchd", "--dry-run", flag, value])
                self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
