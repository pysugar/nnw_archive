#!/usr/bin/env python3
"""Core data layer for the NetNewsWire archive tool.

This tool reads NetNewsWire's SQLite database through the SQLite backup API and
merges articles into a separate append-friendly archive database. It never
writes to the NetNewsWire source database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import html.parser
import json
import logging
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TOOL_NAME = "nnw_archive"
NATIVE_SOURCE_DB = Path(
    "~/Library/Containers/com.ranchero.NetNewsWire-Evergreen/Data/Library/"
    "Application Support/NetNewsWire/Accounts/OnMyMac/DB.sqlite3"
).expanduser()
FALLBACK_SOURCE_DB_NAME = "netnewswire.sqlite3"
DEFAULT_SOURCE_DB = NATIVE_SOURCE_DB
DEFAULT_LABEL = "local.netnewswire-archive.sync"
ARCHIVE_SCHEMA_VERSION = "1"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

logger = logging.getLogger(TOOL_NAME)
logger.addHandler(logging.NullHandler())

ARTICLE_COLUMNS = [
    "articleID",
    "feedID",
    "uniqueID",
    "title",
    "contentHTML",
    "contentText",
    "markdown",
    "url",
    "externalURL",
    "summary",
    "imageURL",
    "bannerImageURL",
    "datePublished",
    "dateModified",
    "searchRowID",
]
ARTICLE_HASH_COLUMNS = [column for column in ARTICLE_COLUMNS if column != "searchRowID"]
STATUS_COLUMNS = ["articleID", "read", "starred", "dateArrived"]
AUTHOR_COLUMNS = ["authorID", "name", "url", "avatarURL", "emailAddress"]
AUTHOR_LOOKUP_COLUMNS = ["authorID", "articleID"]

REQUIRED_SOURCE_COLUMNS = {
    "articles": set(ARTICLE_COLUMNS),
    "statuses": set(STATUS_COLUMNS),
    "authors": set(AUTHOR_COLUMNS),
    "authorsLookup": set(AUTHOR_LOOKUP_COLUMNS),
}


@dataclass
class SyncResult:
    archive_db: str
    source_db: str
    run_id: int
    source_schema_hash: str
    articles_seen: int = 0
    articles_inserted: int = 0
    articles_updated: int = 0
    statuses_seen: int = 0
    statuses_updated: int = 0
    authors_seen: int = 0
    authors_upserted: int = 0
    author_links_seen: int = 0
    author_links_upserted: int = 0
    feeds_seen: int = 0
    feeds_updated: int = 0
    source_deleted_marked: int = 0


def utc_now_iso() -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def default_state_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "netnewswire-archive"
    return Path.home() / ".local" / "state" / "netnewswire-archive"


def default_log_path(data_dir: Path | None = None) -> Path:
    return (data_dir or default_state_dir()).expanduser() / "nnw_archive.log"


def setup_logging(
    *,
    data_dir: Path | None = None,
    verbose: bool = False,
    level: int = logging.INFO,
) -> Path:
    log_path = default_log_path(data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(TOOL_NAME)
    root.setLevel(logging.DEBUG if verbose else level)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        stream_handler.setLevel(logging.DEBUG)
        root.addHandler(stream_handler)

    return log_path


def resolve_source_db(
    source_db: Path | None = None,
    *,
    cwd: Path | None = None,
    preferred_source: Path | None = None,
) -> Path:
    if source_db is not None:
        path = source_db.expanduser()
        if not path.exists():
            raise FileNotFoundError(f"NetNewsWire source database not found: {path}")
        return path.resolve()

    preferred = (preferred_source or NATIVE_SOURCE_DB).expanduser()
    if preferred.exists():
        return preferred.resolve()

    fallback = (cwd or Path.cwd()) / FALLBACK_SOURCE_DB_NAME
    fallback = fallback.expanduser()
    if fallback.exists():
        return fallback.resolve()

    raise FileNotFoundError(
        "NetNewsWire source database not found. Tried native path "
        f"{preferred} and fallback {fallback}. Pass --source to override."
    )


def resolve_archive_db(data_dir: Path | None = None, archive_db: Path | None = None) -> Path:
    if archive_db is not None:
        return archive_db.expanduser()
    return (data_dir or default_state_dir()).expanduser() / "archive.sqlite3"


def sqlite_file_uri(path: Path, *, mode: str | None = None, immutable: bool = False) -> str:
    uri = path.expanduser().resolve().as_uri()
    params: dict[str, str] = {}
    if mode:
        params["mode"] = mode
    if immutable:
        params["immutable"] = "1"
    if params:
        uri = f"{uri}?{urllib.parse.urlencode(params)}"
    return uri


def connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_file_uri(path, mode="ro"), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_archive(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def sqlite_snapshot(source_path: Path, *, label: str = "source"):
    with tempfile.TemporaryDirectory(prefix=f"nnw-archive-{label}-") as tmpdir:
        snapshot_path = Path(tmpdir) / "snapshot.sqlite3"
        source = connect_readonly(source_path)
        try:
            target = sqlite3.connect(snapshot_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

        conn = sqlite3.connect(snapshot_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


def ensure_archive_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS articles (
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

        CREATE TABLE IF NOT EXISTS statuses (
            articleID TEXT NOT NULL PRIMARY KEY,
            read BOOL NOT NULL DEFAULT 0,
            starred BOOL NOT NULL DEFAULT 0,
            dateArrived DATE NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS authors (
            authorID TEXT NOT NULL PRIMARY KEY,
            name TEXT,
            url TEXT,
            avatarURL TEXT,
            emailAddress TEXT
        );

        CREATE TABLE IF NOT EXISTS authorsLookup (
            authorID TEXT NOT NULL,
            articleID TEXT NOT NULL,
            PRIMARY KEY(authorID, articleID)
        );

        CREATE INDEX IF NOT EXISTS articles_feedID_datePublished_articleID
            ON articles(feedID, datePublished, articleID);
        CREATE INDEX IF NOT EXISTS statuses_starred_index
            ON statuses(starred);

        CREATE TABLE IF NOT EXISTS archive_metadata (
            key TEXT NOT NULL PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS archive_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            source_db TEXT NOT NULL,
            source_account_dir TEXT,
            source_schema_hash TEXT NOT NULL,
            articles_seen INTEGER NOT NULL DEFAULT 0,
            articles_inserted INTEGER NOT NULL DEFAULT 0,
            articles_updated INTEGER NOT NULL DEFAULT 0,
            statuses_seen INTEGER NOT NULL DEFAULT 0,
            statuses_updated INTEGER NOT NULL DEFAULT 0,
            authors_seen INTEGER NOT NULL DEFAULT 0,
            authors_upserted INTEGER NOT NULL DEFAULT 0,
            author_links_seen INTEGER NOT NULL DEFAULT 0,
            author_links_upserted INTEGER NOT NULL DEFAULT 0,
            feeds_seen INTEGER NOT NULL DEFAULT 0,
            feeds_updated INTEGER NOT NULL DEFAULT 0,
            source_deleted_marked INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS archive_article_meta (
            articleID TEXT NOT NULL PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source_deleted_at TEXT,
            article_hash TEXT NOT NULL,
            status_hash TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS archive_article_meta_source_deleted_at
            ON archive_article_meta(source_deleted_at);

        CREATE TABLE IF NOT EXISTS archive_feeds (
            feedID TEXT NOT NULL PRIMARY KEY,
            feedURL TEXT,
            title TEXT,
            display_name TEXT,
            folder_path TEXT,
            homePageURL TEXT,
            iconURL TEXT,
            faviconURL TEXT,
            editedName TEXT,
            externalID TEXT,
            lastCheckDate REAL,
            source_hash TEXT NOT NULL,
            source_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS archive_feeds_folder_path
            ON archive_feeds(folder_path);

        CREATE TABLE IF NOT EXISTS archive_feed_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            snapshot_at TEXT NOT NULL,
            feedID TEXT NOT NULL,
            feedURL TEXT,
            title TEXT,
            display_name TEXT,
            folder_path TEXT,
            homePageURL TEXT,
            iconURL TEXT,
            faviconURL TEXT,
            editedName TEXT,
            externalID TEXT,
            lastCheckDate REAL,
            source_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS archive_search_docs (
            docid INTEGER PRIMARY KEY AUTOINCREMENT,
            articleID TEXT NOT NULL UNIQUE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS archive_search USING fts5(
            articleID UNINDEXED,
            feedID UNINDEXED,
            title,
            body,
            summary,
            url UNINDEXED
        );
        """
    )
    conn.execute(
        """
        INSERT INTO archive_metadata(key, value)
        VALUES('archive_schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (ARCHIVE_SCHEMA_VERSION,),
    )
    conn.commit()


def validate_source_schema(conn: sqlite3.Connection) -> None:
    for table, required_columns in REQUIRED_SOURCE_COLUMNS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            raise RuntimeError(f"Source database is missing required table: {table}")
        present_columns = {row["name"] for row in rows}
        missing = sorted(required_columns - present_columns)
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(f"Source table {table} is missing columns: {joined}")


def source_schema_hash(conn: sqlite3.Connection) -> str:
    table_names = tuple(REQUIRED_SOURCE_COLUMNS)
    placeholders = ",".join("?" for _ in table_names)
    rows = conn.execute(
        f"""
        SELECT type, name, tbl_name, COALESCE(sql, '') AS sql
        FROM sqlite_master
        WHERE (name IN ({placeholders}) OR tbl_name IN ({placeholders}))
          AND type IN ('table', 'index', 'trigger', 'view')
        ORDER BY type, name
        """,
        (*table_names, *table_names),
    ).fetchall()
    payload = "\n".join(
        f"{row['type']}|{row['name']}|{row['tbl_name']}|{row['sql']}" for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM archive_metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO archive_metadata(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def normalize_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=normalize_value)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def datetime_to_timestamp(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.timestamp()
    if isinstance(value, dt.date):
        combined = dt.datetime.combine(value, dt.time(), tzinfo=dt.timezone.utc)
        return combined.timestamp()
    return value


def row_to_dict(row: sqlite3.Row, columns: list[str]) -> dict[str, Any]:
    return {column: row[column] for column in columns}


def hash_row(row: dict[str, Any], columns: list[str]) -> str:
    return stable_hash({column: row.get(column) for column in columns})


def select_row(
    conn: sqlite3.Connection, table: str, pk_column: str, pk_value: Any, columns: list[str]
) -> dict[str, Any] | None:
    column_sql = ", ".join(columns)
    row = conn.execute(
        f"SELECT {column_sql} FROM {table} WHERE {pk_column} = ?", (pk_value,)
    ).fetchone()
    if row is None:
        return None
    return row_to_dict(row, columns)


def upsert_if_changed(
    conn: sqlite3.Connection,
    table: str,
    pk_column: str,
    columns: list[str],
    row: dict[str, Any],
) -> str:
    existing = select_row(conn, table, pk_column, row[pk_column], columns)
    if existing is None:
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        conn.execute(
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
            tuple(row.get(column) for column in columns),
        )
        return "inserted"

    incoming = {column: row.get(column) for column in columns}
    if existing == incoming:
        return "same"

    update_columns = [column for column in columns if column != pk_column]
    set_sql = ", ".join(f"{column} = ?" for column in update_columns)
    conn.execute(
        f"UPDATE {table} SET {set_sql} WHERE {pk_column} = ?",
        tuple(row.get(column) for column in update_columns) + (row[pk_column],),
    )
    return "updated"


class HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        return " ".join(self.parts)


def html_to_text(content_html: str | None) -> str:
    if not content_html:
        return ""
    parser = HTMLTextExtractor()
    try:
        parser.feed(content_html)
    except Exception:
        return ""
    return parser.get_text()


def article_search_body(article: dict[str, Any]) -> str:
    parts = [
        article.get("contentText"),
        article.get("markdown"),
        article.get("summary"),
        html_to_text(article.get("contentHTML")),
    ]
    return "\n".join(str(part) for part in parts if part)


def update_search_doc(conn: sqlite3.Connection, article: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO archive_search_docs(articleID) VALUES(?)",
        (article["articleID"],),
    )
    docid = conn.execute(
        "SELECT docid FROM archive_search_docs WHERE articleID = ?",
        (article["articleID"],),
    ).fetchone()["docid"]
    conn.execute("DELETE FROM archive_search WHERE rowid = ?", (docid,))
    conn.execute(
        """
        INSERT INTO archive_search(rowid, articleID, feedID, title, body, summary, url)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            docid,
            article.get("articleID"),
            article.get("feedID"),
            article.get("title"),
            article_search_body(article),
            article.get("summary"),
            article.get("url") or article.get("externalURL"),
        ),
    )


def read_feed_settings(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}

    feeds: dict[str, dict[str, Any]] = {}
    try:
        with sqlite_snapshot(path, label="feedsettings") as conn:
            rows = conn.execute("SELECT * FROM feedSettings").fetchall()
            for row in rows:
                record = dict(row)
                feed_url = record.get("feedURL")
                feed_id = record.get("feedID") or feed_url
                if feed_id:
                    feeds[str(feed_id)] = record
    except Exception:
        logger.warning("Failed to read FeedSettings metadata from %s", path, exc_info=True)
    return feeds


def read_opml(path: Path | None, url_to_feed_id: dict[str, str]) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}

    try:
        tree = ET.parse(path)
        root = tree.getroot()
        body = root.find("body")
    except Exception:
        logger.warning("Failed to read OPML metadata from %s", path, exc_info=True)
        return {}

    feeds: dict[str, dict[str, Any]] = {}

    def outline_label(element: ET.Element) -> str:
        return element.attrib.get("title") or element.attrib.get("text") or ""

    def walk(element: ET.Element, folders: list[str]) -> None:
        xml_url = element.attrib.get("xmlUrl")
        if xml_url:
            feed_id = url_to_feed_id.get(xml_url, xml_url)
            feeds[feed_id] = {
                "feedID": feed_id,
                "feedURL": xml_url,
                "title": element.attrib.get("title") or element.attrib.get("text"),
                "text": element.attrib.get("text"),
                "htmlUrl": element.attrib.get("htmlUrl"),
                "folder_path": "/".join(folder for folder in folders if folder),
            }
            return

        label = outline_label(element)
        next_folders = folders + ([label] if label else [])
        for child in element.findall("outline"):
            walk(child, next_folders)

    for child in body.findall("outline"):
        walk(child, [])

    return feeds


def read_feed_metadata(path: Path | None, url_to_feed_id: dict[str, str]) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}

    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
    except Exception:
        logger.warning("Failed to read feed metadata plist from %s", path, exc_info=True)
        return {}

    if not isinstance(plist, dict):
        return {}

    feeds: dict[str, dict[str, Any]] = {}
    for feed_url, metadata in plist.items():
        if not isinstance(metadata, dict):
            continue
        feed_id = str(metadata.get("feedID") or url_to_feed_id.get(feed_url, feed_url))
        feeds[feed_id] = {"feedID": feed_id, "feedURL": feed_url, **metadata}
    return feeds


def compact_feed_record(record: dict[str, Any]) -> dict[str, Any]:
    edited_name = record.get("editedName")
    title = record.get("title") or record.get("text")
    display_name = edited_name or title or record.get("feedURL") or record.get("feedID")
    return {
        "feedID": record.get("feedID"),
        "feedURL": record.get("feedURL"),
        "title": title,
        "display_name": display_name,
        "folder_path": record.get("folder_path"),
        "homePageURL": record.get("homePageURL") or record.get("htmlUrl"),
        "iconURL": record.get("iconURL"),
        "faviconURL": record.get("faviconURL"),
        "editedName": edited_name,
        "externalID": record.get("externalID"),
        "lastCheckDate": datetime_to_timestamp(record.get("lastCheckDate")),
        "source_json": stable_json(record),
    }


def merge_feed_metadata(
    *,
    source_feed_ids: set[str],
    feed_settings_db: Path | None,
    opml_path: Path | None,
    feed_metadata_path: Path | None,
) -> list[dict[str, Any]]:
    settings = read_feed_settings(feed_settings_db)
    url_to_feed_id = {
        str(record["feedURL"]): feed_id
        for feed_id, record in settings.items()
        if record.get("feedURL")
    }
    opml = read_opml(opml_path, url_to_feed_id)
    plist = read_feed_metadata(feed_metadata_path, url_to_feed_id)

    merged: dict[str, dict[str, Any]] = {}
    for feed_id, record in settings.items():
        merged[feed_id] = {"feedID": feed_id, **record}
    for feed_id, record in plist.items():
        merged.setdefault(feed_id, {"feedID": feed_id}).update(record)
    for feed_id, record in opml.items():
        merged.setdefault(feed_id, {"feedID": feed_id}).update(
            {key: value for key, value in record.items() if value not in (None, "")}
        )

    for feed_id in source_feed_ids:
        merged.setdefault(feed_id, {"feedID": feed_id, "feedURL": feed_id})

    return [compact_feed_record(record) for _, record in sorted(merged.items())]


def sync_feed_records(
    conn: sqlite3.Connection, run_id: int, now: str, feeds: list[dict[str, Any]]
) -> int:
    updated = 0
    for feed in feeds:
        feed_id = feed["feedID"]
        source_hash = stable_hash(
            {key: feed.get(key) for key in feed if key not in {"source_hash"}}
        )
        existing = conn.execute(
            "SELECT source_hash, created_at FROM archive_feeds WHERE feedID = ?",
            (feed_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO archive_feeds(
                    feedID, feedURL, title, display_name, folder_path, homePageURL,
                    iconURL, faviconURL, editedName, externalID, lastCheckDate,
                    source_hash, source_json, created_at, updated_at, last_seen_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feed_id,
                    feed.get("feedURL"),
                    feed.get("title"),
                    feed.get("display_name"),
                    feed.get("folder_path"),
                    feed.get("homePageURL"),
                    feed.get("iconURL"),
                    feed.get("faviconURL"),
                    feed.get("editedName"),
                    feed.get("externalID"),
                    feed.get("lastCheckDate"),
                    source_hash,
                    feed.get("source_json"),
                    now,
                    now,
                    now,
                ),
            )
            updated += 1
        elif existing["source_hash"] != source_hash:
            conn.execute(
                """
                UPDATE archive_feeds
                SET feedURL = ?, title = ?, display_name = ?, folder_path = ?,
                    homePageURL = ?, iconURL = ?, faviconURL = ?, editedName = ?,
                    externalID = ?, lastCheckDate = ?, source_hash = ?,
                    source_json = ?, updated_at = ?, last_seen_at = ?
                WHERE feedID = ?
                """,
                (
                    feed.get("feedURL"),
                    feed.get("title"),
                    feed.get("display_name"),
                    feed.get("folder_path"),
                    feed.get("homePageURL"),
                    feed.get("iconURL"),
                    feed.get("faviconURL"),
                    feed.get("editedName"),
                    feed.get("externalID"),
                    feed.get("lastCheckDate"),
                    source_hash,
                    feed.get("source_json"),
                    now,
                    now,
                    feed_id,
                ),
            )
            updated += 1
        else:
            conn.execute(
                "UPDATE archive_feeds SET last_seen_at = ? WHERE feedID = ?",
                (now, feed_id),
            )

        conn.execute(
            """
            INSERT INTO archive_feed_snapshots(
                run_id, snapshot_at, feedID, feedURL, title, display_name,
                folder_path, homePageURL, iconURL, faviconURL, editedName,
                externalID, lastCheckDate, source_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                now,
                feed_id,
                feed.get("feedURL"),
                feed.get("title"),
                feed.get("display_name"),
                feed.get("folder_path"),
                feed.get("homePageURL"),
                feed.get("iconURL"),
                feed.get("faviconURL"),
                feed.get("editedName"),
                feed.get("externalID"),
                feed.get("lastCheckDate"),
                feed.get("source_json"),
            ),
        )
    return updated


def start_run(
    conn: sqlite3.Connection,
    *,
    now: str,
    source_db: Path,
    source_account_dir: Path,
    schema_hash: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO archive_runs(started_at, source_db, source_account_dir, source_schema_hash)
        VALUES(?, ?, ?, ?)
        """,
        (now, str(source_db), str(source_account_dir), schema_hash),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_run(conn: sqlite3.Connection, result: SyncResult, *, now: str, error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE archive_runs
        SET finished_at = ?, articles_seen = ?, articles_inserted = ?,
            articles_updated = ?, statuses_seen = ?, statuses_updated = ?,
            authors_seen = ?, authors_upserted = ?, author_links_seen = ?,
            author_links_upserted = ?, feeds_seen = ?, feeds_updated = ?,
            source_deleted_marked = ?, error = ?
        WHERE run_id = ?
        """,
        (
            now,
            result.articles_seen,
            result.articles_inserted,
            result.articles_updated,
            result.statuses_seen,
            result.statuses_updated,
            result.authors_seen,
            result.authors_upserted,
            result.author_links_seen,
            result.author_links_upserted,
            result.feeds_seen,
            result.feeds_updated,
            result.source_deleted_marked,
            error,
            result.run_id,
        ),
    )
    conn.commit()


def enforce_schema_hash(
    conn: sqlite3.Connection, schema_hash: str, *, allow_schema_change: bool
) -> None:
    previous_hash = get_metadata(conn, "source_schema_hash")
    if previous_hash and previous_hash != schema_hash and not allow_schema_change:
        raise RuntimeError(
            "Source schema hash changed. Re-run with --allow-schema-change after "
            "reviewing the NetNewsWire schema."
        )
    set_metadata(conn, "source_schema_hash", schema_hash)


def sync_archive(
    *,
    source_db: Path | None = None,
    data_dir: Path | None = None,
    archive_db: Path | None = None,
    feed_settings_db: Path | None = None,
    opml_path: Path | None = None,
    feed_metadata_path: Path | None = None,
    allow_schema_change: bool = False,
    now: str | None = None,
) -> SyncResult:
    now = now or utc_now_iso()
    resolved_source_db = resolve_source_db(source_db)
    source_account_dir = resolved_source_db.parent
    archive_db = resolve_archive_db(data_dir, archive_db)
    logger.info("Starting NetNewsWire archive sync")
    logger.info("Source database: %s", resolved_source_db)
    logger.info("Archive database: %s", archive_db)

    if feed_settings_db is None:
        feed_settings_db = source_account_dir / "FeedSettings.db"
    if opml_path is None:
        opml_path = source_account_dir / "Subscriptions.opml"
    if feed_metadata_path is None:
        feed_metadata_path = source_account_dir / "FeedMetadata.plist"

    archive = connect_archive(archive_db)
    try:
        ensure_archive_schema(archive)
    except Exception:
        logger.exception("Failed to initialize archive schema")
        archive.close()
        raise

    with sqlite_snapshot(resolved_source_db, label="articles") as source:
        try:
            validate_source_schema(source)
            schema_hash = source_schema_hash(source)
            enforce_schema_hash(
                archive, schema_hash, allow_schema_change=allow_schema_change
            )
        except Exception:
            logger.exception("Failed to validate NetNewsWire source schema")
            archive.close()
            raise

        run_id = start_run(
            archive,
            now=now,
            source_db=resolved_source_db,
            source_account_dir=source_account_dir,
            schema_hash=schema_hash,
        )
        result = SyncResult(
            archive_db=str(archive_db),
            source_db=str(resolved_source_db),
            run_id=run_id,
            source_schema_hash=schema_hash,
        )

        try:
            with archive:
                articles = [
                    row_to_dict(row, ARTICLE_COLUMNS)
                    for row in source.execute(
                        f"SELECT {', '.join(ARTICLE_COLUMNS)} FROM articles"
                    )
                ]
                statuses = [
                    row_to_dict(row, STATUS_COLUMNS)
                    for row in source.execute(
                        f"SELECT {', '.join(STATUS_COLUMNS)} FROM statuses"
                    )
                ]
                authors = [
                    row_to_dict(row, AUTHOR_COLUMNS)
                    for row in source.execute(
                        f"SELECT {', '.join(AUTHOR_COLUMNS)} FROM authors"
                    )
                ]
                author_links = [
                    row_to_dict(row, AUTHOR_LOOKUP_COLUMNS)
                    for row in source.execute(
                        f"SELECT {', '.join(AUTHOR_LOOKUP_COLUMNS)} FROM authorsLookup"
                    )
                ]

                result.articles_seen = len(articles)
                result.statuses_seen = len(statuses)
                result.authors_seen = len(authors)
                result.author_links_seen = len(author_links)

                article_changed: set[str] = set()
                current_article_ids = {str(article["articleID"]) for article in articles}

                archive.execute("DROP TABLE IF EXISTS temp.current_article_ids")
                archive.execute(
                    "CREATE TEMP TABLE current_article_ids(articleID TEXT PRIMARY KEY)"
                )
                archive.executemany(
                    "INSERT INTO current_article_ids(articleID) VALUES(?)",
                    [(article_id,) for article_id in current_article_ids],
                )

                for article in articles:
                    status = upsert_if_changed(
                        archive, "articles", "articleID", ARTICLE_COLUMNS, article
                    )
                    if status == "inserted":
                        result.articles_inserted += 1
                        article_changed.add(str(article["articleID"]))
                    elif status == "updated":
                        result.articles_updated += 1
                        article_changed.add(str(article["articleID"]))

                status_changed: set[str] = set()
                status_hash_by_article: dict[str, str] = {}
                for status_row in statuses:
                    status_hash = hash_row(status_row, STATUS_COLUMNS)
                    status_hash_by_article[str(status_row["articleID"])] = status_hash
                    status = upsert_if_changed(
                        archive, "statuses", "articleID", STATUS_COLUMNS, status_row
                    )
                    if status in {"inserted", "updated"}:
                        result.statuses_updated += 1
                        status_changed.add(str(status_row["articleID"]))

                for author in authors:
                    status = upsert_if_changed(
                        archive, "authors", "authorID", AUTHOR_COLUMNS, author
                    )
                    if status in {"inserted", "updated"}:
                        result.authors_upserted += 1

                for link in author_links:
                    before = archive.total_changes
                    archive.execute(
                        """
                        INSERT OR IGNORE INTO authorsLookup(authorID, articleID)
                        VALUES(?, ?)
                        """,
                        (link["authorID"], link["articleID"]),
                    )
                    if archive.total_changes > before:
                        result.author_links_upserted += 1

                for article in articles:
                    article_id = str(article["articleID"])
                    article_hash = hash_row(article, ARTICLE_HASH_COLUMNS)
                    status_hash = status_hash_by_article.get(article_id, "")
                    existing_meta = archive.execute(
                        """
                        SELECT created_at, updated_at, article_hash, status_hash
                        FROM archive_article_meta
                        WHERE articleID = ?
                        """,
                        (article_id,),
                    ).fetchone()

                    changed = (
                        article_id in article_changed
                        or article_id in status_changed
                        or existing_meta is None
                    )
                    if existing_meta is None:
                        archive.execute(
                            """
                            INSERT INTO archive_article_meta(
                                articleID, created_at, updated_at, last_seen_at,
                                source_deleted_at, article_hash, status_hash
                            )
                            VALUES(?, ?, ?, ?, NULL, ?, ?)
                            """,
                            (article_id, now, now, now, article_hash, status_hash),
                        )
                    else:
                        archive.execute(
                            """
                            UPDATE archive_article_meta
                            SET updated_at = ?, last_seen_at = ?, source_deleted_at = NULL,
                                article_hash = ?, status_hash = ?
                            WHERE articleID = ?
                            """,
                            (
                                now if changed else existing_meta["updated_at"],
                                now,
                                article_hash,
                                status_hash,
                                article_id,
                            ),
                        )

                    if article_id in article_changed or existing_meta is None:
                        update_search_doc(archive, article)

                before_deleted = archive.total_changes
                archive.execute(
                    """
                    UPDATE archive_article_meta
                    SET source_deleted_at = ?
                    WHERE source_deleted_at IS NULL
                      AND NOT EXISTS (
                        SELECT 1
                        FROM current_article_ids c
                        WHERE c.articleID = archive_article_meta.articleID
                      )
                    """,
                    (now,),
                )
                result.source_deleted_marked = archive.total_changes - before_deleted
                archive.execute("DROP TABLE IF EXISTS temp.current_article_ids")

                feed_records = merge_feed_metadata(
                    source_feed_ids={str(article["feedID"]) for article in articles},
                    feed_settings_db=feed_settings_db,
                    opml_path=opml_path,
                    feed_metadata_path=feed_metadata_path,
                )
                result.feeds_seen = len(feed_records)
                result.feeds_updated = sync_feed_records(archive, run_id, now, feed_records)

            finish_run(archive, result, now=utc_now_iso())
            logger.info(
                "Sync completed: %s inserted, %s updated, %s statuses updated, %s feeds updated",
                result.articles_inserted,
                result.articles_updated,
                result.statuses_updated,
                result.feeds_updated,
            )
            return result
        except Exception as exc:
            logger.exception("Sync failed")
            finish_run(archive, result, now=utc_now_iso(), error=str(exc))
            raise
        finally:
            archive.close()


def get_stats(archive_db: Path) -> dict[str, Any]:
    conn = connect_archive(archive_db)
    ensure_archive_schema(conn)
    try:
        last_run = conn.execute(
            """
            SELECT run_id, started_at, finished_at, source_db, error
            FROM archive_runs
            ORDER BY run_id DESC
            LIMIT 1
            """
        ).fetchone()
        return {
            "archive_db": str(archive_db),
            "articles": conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"],
            "feeds": conn.execute("SELECT COUNT(*) AS c FROM archive_feeds").fetchone()["c"],
            "starred": conn.execute(
                "SELECT COUNT(*) AS c FROM statuses WHERE starred = 1"
            ).fetchone()["c"],
            "unread": conn.execute(
                "SELECT COUNT(*) AS c FROM statuses WHERE read = 0"
            ).fetchone()["c"],
            "source_deleted": conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM archive_article_meta
                WHERE source_deleted_at IS NOT NULL
                """
            ).fetchone()["c"],
            "last_run": dict(last_run) if last_run else None,
        }
    finally:
        conn.close()


def parse_date_filter(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    if len(value) == 10:
        parsed = dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc)
    else:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def bool_from_param(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def quote_fts_query(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.strip().split() if token]
    if not tokens:
        return '""'
    return " AND ".join(f'"{token}"' for token in tokens)


def search_conditions(
    *,
    q: str = "",
    feed_id: str | None = None,
    folder: str | None = None,
    starred: bool | None = None,
    unread: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[str], list[Any]]:
    params: list[Any] = []
    where = ["1 = 1"]

    if q.strip():
        where.append(
            "a.articleID IN (SELECT articleID FROM archive_search WHERE archive_search MATCH ?)"
        )
        params.append(quote_fts_query(q))
    if feed_id:
        where.append("a.feedID = ?")
        params.append(feed_id)
    if folder:
        where.append("COALESCE(f.folder_path, '') = ?")
        params.append(folder)
    if starred is not None:
        where.append("COALESCE(s.starred, 0) = ?")
        params.append(1 if starred else 0)
    if unread is not None:
        where.append("COALESCE(s.read, 0) = ?")
        params.append(0 if unread else 1)

    from_ts = parse_date_filter(date_from)
    to_ts = parse_date_filter(date_to)
    if from_ts is not None:
        where.append("COALESCE(a.datePublished, s.dateArrived, 0) >= ?")
        params.append(from_ts)
    if to_ts is not None:
        where.append("COALESCE(a.datePublished, s.dateArrived, 0) <= ?")
        params.append(to_ts)

    return where, params


def fallback_search_where(where: list[str], params: list[Any], q: str) -> tuple[list[str], list[Any]]:
    fallback_where = [
        """
        LOWER(
            COALESCE(a.title, '') || ' ' ||
            COALESCE(a.summary, '') || ' ' ||
            COALESCE(a.contentText, '') || ' ' ||
            COALESCE(a.markdown, '')
        ) LIKE ?
        """
    ]
    fallback_where.extend(condition for condition in where if "archive_search" not in condition)
    return fallback_where, [f"%{q.lower()}%"] + params[1:]


def search_articles(
    archive_db: Path,
    *,
    q: str = "",
    feed_id: str | None = None,
    folder: str | None = None,
    starred: bool | None = None,
    unread: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = search_conditions(
        q=q,
        feed_id=feed_id,
        folder=folder,
        starred=starred,
        unread=unread,
        date_from=date_from,
        date_to=date_to,
    )

    sql = f"""
        SELECT
            a.articleID, a.feedID, a.title, a.url, a.externalURL, a.summary,
            a.imageURL, a.bannerImageURL, a.datePublished, a.dateModified,
            s.read, s.starred, s.dateArrived,
            f.display_name AS feedTitle, f.feedURL, f.folder_path,
            m.created_at, m.updated_at, m.last_seen_at, m.source_deleted_at
        FROM articles a
        LEFT JOIN statuses s ON s.articleID = a.articleID
        LEFT JOIN archive_feeds f ON f.feedID = a.feedID
        LEFT JOIN archive_article_meta m ON m.articleID = a.articleID
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(a.datePublished, s.dateArrived, 0) DESC, a.articleID DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    conn = connect_archive(archive_db)
    try:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            if not q.strip():
                raise
            fallback_where, fallback_params = fallback_search_where(where, params, q)
            fallback_sql = sql.replace(
                "WHERE " + " AND ".join(where),
                "WHERE " + " AND ".join(fallback_where),
            )
            rows = conn.execute(fallback_sql, fallback_params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_articles(
    archive_db: Path,
    *,
    q: str = "",
    feed_id: str | None = None,
    folder: str | None = None,
    starred: bool | None = None,
    unread: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    where, params = search_conditions(
        q=q,
        feed_id=feed_id,
        folder=folder,
        starred=starred,
        unread=unread,
        date_from=date_from,
        date_to=date_to,
    )
    sql = f"""
        SELECT COUNT(*) AS total
        FROM articles a
        LEFT JOIN statuses s ON s.articleID = a.articleID
        LEFT JOIN archive_feeds f ON f.feedID = a.feedID
        LEFT JOIN archive_article_meta m ON m.articleID = a.articleID
        WHERE {' AND '.join(where)}
    """

    conn = connect_archive(archive_db)
    try:
        try:
            return int(conn.execute(sql, params).fetchone()["total"])
        except sqlite3.OperationalError:
            if not q.strip():
                raise
            fallback_where, fallback_params = fallback_search_where(where, params, q)
            fallback_sql = sql.replace(
                "WHERE " + " AND ".join(where),
                "WHERE " + " AND ".join(fallback_where),
            )
            return int(conn.execute(fallback_sql, fallback_params).fetchone()["total"])
    finally:
        conn.close()


def search_articles_page(
    archive_db: Path,
    *,
    q: str = "",
    feed_id: str | None = None,
    folder: str | None = None,
    starred: bool | None = None,
    unread: bool | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = max(10, min(page_size, 200))
    offset = (page - 1) * page_size
    filters = {
        "q": q,
        "feed_id": feed_id,
        "folder": folder,
        "starred": starred,
        "unread": unread,
        "date_from": date_from,
        "date_to": date_to,
    }
    total = count_articles(archive_db, **filters)
    items = search_articles(archive_db, limit=page_size, offset=offset, **filters)
    pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
    }


def get_article(archive_db: Path, article_id: str) -> dict[str, Any] | None:
    conn = connect_archive(archive_db)
    try:
        row = conn.execute(
            """
            SELECT
                a.*, s.read, s.starred, s.dateArrived,
                f.display_name AS feedTitle, f.feedURL, f.folder_path,
                m.created_at, m.updated_at, m.last_seen_at, m.source_deleted_at
            FROM articles a
            LEFT JOIN statuses s ON s.articleID = a.articleID
            LEFT JOIN archive_feeds f ON f.feedID = a.feedID
            LEFT JOIN archive_article_meta m ON m.articleID = a.articleID
            WHERE a.articleID = ?
            """,
            (article_id,),
        ).fetchone()
        if row is None:
            return None
        article = dict(row)
        authors = conn.execute(
            """
            SELECT au.*
            FROM authors au
            JOIN authorsLookup l ON l.authorID = au.authorID
            WHERE l.articleID = ?
            ORDER BY au.name
            """,
            (article_id,),
        ).fetchall()
        article["authors"] = [dict(author) for author in authors]
        return article
    finally:
        conn.close()


def list_feeds(archive_db: Path) -> list[dict[str, Any]]:
    conn = connect_archive(archive_db)
    try:
        rows = conn.execute(
            """
            SELECT f.*, COUNT(a.articleID) AS article_count
            FROM archive_feeds f
            LEFT JOIN articles a ON a.feedID = f.feedID
            GROUP BY f.feedID
            ORDER BY COALESCE(f.folder_path, ''), COALESCE(f.display_name, f.feedID)
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NetNewsWire Archive</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #202124;
      --muted: #63666a;
      --line: #d7d8d0;
      --accent: #0f766e;
      --accent-soft: #d9f1ee;
      --danger: #9f1239;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #151617;
        --panel: #202224;
        --ink: #f2f2ef;
        --muted: #a6a8ab;
        --line: #383b40;
        --accent: #2dd4bf;
        --accent-soft: #123d39;
        --danger: #fb7185;
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
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    .stats {
      color: var(--muted);
      margin-left: auto;
      white-space: nowrap;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(320px, 420px) 1fr;
      min-height: calc(100vh - 57px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--panel);
      min-width: 0;
    }
    .filters {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    input, select, button {
      min-height: 34px;
      border: 1px solid var(--line);
      background: var(--bg);
      color: var(--ink);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }
    button {
      cursor: pointer;
      background: var(--accent);
      color: #fff;
      border-color: transparent;
      min-width: 38px;
    }
    .toggles {
      display: flex;
      gap: 10px;
      padding: 0 12px 12px;
      color: var(--muted);
    }
    .toggles label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .results {
      overflow: auto;
      max-height: calc(100vh - 145px);
    }
    .item {
      display: block;
      width: 100%;
      text-align: left;
      border: 0;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      color: var(--ink);
      background: transparent;
      padding: 12px;
    }
    .item:hover, .item.active {
      background: var(--accent-soft);
    }
    .item-title {
      font-weight: 620;
      overflow-wrap: anywhere;
    }
    .item-meta {
      color: var(--muted);
      margin-top: 4px;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    .article-head {
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    .article-head h2 {
      margin: 0 0 8px;
      font-size: 22px;
      letter-spacing: 0;
      overflow-wrap: anywhere;
    }
    .article-meta {
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
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
    .deleted {
      color: var(--danger);
    }
    @media (max-width: 820px) {
      .layout { grid-template-columns: 1fr; }
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
        <button type="submit" title="Search">Search</button>
      </form>
      <div class="toggles">
        <label><input id="starred" type="checkbox"> Starred</label>
        <label><input id="unread" type="checkbox"> Unread</label>
      </div>
      <div class="results" id="results"></div>
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
    const results = document.getElementById('results');
    const filters = document.getElementById('filters');
    const feed = document.getElementById('feed');
    const q = document.getElementById('q');
    const starred = document.getElementById('starred');
    const unread = document.getElementById('unread');
    const stats = document.getElementById('stats');
    const title = document.getElementById('article-title');
    const meta = document.getElementById('article-meta');
    const frame = document.getElementById('article-frame');

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

    async function loadStats() {
      const data = await fetch('/api/stats').then(r => r.json());
      stats.textContent = `${data.articles} articles | ${data.feeds} feeds | ${data.source_deleted} retained`;
    }

    async function loadFeeds() {
      const data = await fetch('/api/feeds').then(r => r.json());
      for (const item of data) {
        const option = document.createElement('option');
        option.value = item.feedID;
        option.textContent = `${item.display_name || item.feedID} (${item.article_count})`;
        feed.appendChild(option);
      }
    }

    function renderResults(items) {
      results.replaceChildren();
      if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = 'No results';
        results.appendChild(empty);
        return;
      }
      for (const item of items) {
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
        results.appendChild(button);
      }
    }

    async function search() {
      const params = new URLSearchParams();
      if (q.value) params.set('q', q.value);
      if (feed.value) params.set('feed_id', feed.value);
      if (starred.checked) params.set('starred', '1');
      if (unread.checked) params.set('unread', '1');
      const data = await fetch(`/api/search?${params}`).then(r => r.json());
      renderResults(data);
    }

    async function loadArticle(articleID, button) {
      document.querySelectorAll('.item.active').forEach(el => el.classList.remove('active'));
      button?.classList.add('active');
      const article = await fetch(`/api/articles/${encodeURIComponent(articleID)}`).then(r => r.json());
      title.textContent = article.title || '(untitled)';
      meta.replaceChildren();
      for (const value of [
        article.feedTitle || article.feedID,
        fmtTime(article.datePublished || article.dateArrived),
        article.starred ? 'starred' : '',
        article.source_deleted_at ? 'retained from source deletion' : ''
      ].filter(Boolean)) {
        const span = document.createElement('span');
        span.textContent = value;
        if (value.startsWith('retained')) span.className = 'deleted';
        meta.appendChild(span);
      }
      const body = article.contentHTML || `<pre>${escapeHtml(article.contentText || article.markdown || article.summary || '')}</pre>`;
      frame.srcdoc = `<!doctype html><meta charset="utf-8"><base target="_blank"><style>body{font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:28px;max-width:820px;color:#202124}img{max-width:100%;height:auto}pre{white-space:pre-wrap}</style>${body}`;
    }

    filters.addEventListener('submit', event => { event.preventDefault(); search(); });
    starred.addEventListener('change', search);
    unread.addEventListener('change', search);
    loadStats();
    loadFeeds().then(search);
  </script>
</body>
</html>
"""


class ArchiveRequestHandler(BaseHTTPRequestHandler):
    archive_db: Path
    server_version = "NNWArchive/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, body_text: str, status: int = 200) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self.send_json({"error": "write operations are not supported"}, status=405)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        try:
            if parsed.path in {"/", "/index.html"}:
                self.send_html(INDEX_HTML)
                return

            if parsed.path == "/api/stats":
                self.send_json(get_stats(self.archive_db))
                return

            if parsed.path == "/api/feeds":
                self.send_json(list_feeds(self.archive_db))
                return

            if parsed.path == "/api/search":
                self.send_json(
                    search_articles(
                        self.archive_db,
                        q=params.get("q", [""])[0],
                        feed_id=params.get("feed_id", [None])[0],
                        folder=params.get("folder", [None])[0],
                        starred=bool_from_param(params.get("starred", [None])[0]),
                        unread=bool_from_param(params.get("unread", [None])[0]),
                        date_from=params.get("from", [None])[0],
                        date_to=params.get("to", [None])[0],
                        limit=int(params.get("limit", ["50"])[0]),
                        offset=int(params.get("offset", ["0"])[0]),
                    )
                )
                return

            if parsed.path.startswith("/api/articles/"):
                article_id = urllib.parse.unquote(parsed.path.removeprefix("/api/articles/"))
                article = get_article(self.archive_db, article_id)
                if article is None:
                    self.send_json({"error": "article not found"}, status=404)
                else:
                    self.send_json(article)
                return

            self.send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)


def make_handler(archive_db: Path) -> type[ArchiveRequestHandler]:
    class Handler(ArchiveRequestHandler):
        pass

    Handler.archive_db = archive_db
    return Handler


def run_web(archive_db: Path, *, host: str, port: int) -> None:
    conn = connect_archive(archive_db)
    try:
        ensure_archive_schema(conn)
    finally:
        conn.close()
    handler = make_handler(archive_db)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Serving NetNewsWire archive at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def launchd_plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def launchd_program_arguments(args: argparse.Namespace) -> list[str]:
    uv_path = shutil.which("uv") or "uv"
    script_path = str(Path(__file__).resolve())
    command = [
        uv_path,
        "run",
        "--no-sync",
        "--",
        "python",
        script_path,
        "sync",
        "--source",
        str(args.source),
        "--data-dir",
        str(args.data_dir),
    ]
    if args.archive_db:
        command.extend(["--archive-db", str(args.archive_db)])
    if args.allow_schema_change:
        command.append("--allow-schema-change")
    return command


def install_launchd(args: argparse.Namespace) -> Path:
    path = launchd_plist_path(args.label)
    plist = {
        "Label": args.label,
        "ProgramArguments": launchd_program_arguments(args),
        "StartCalendarInterval": {"Hour": args.hour, "Minute": args.minute},
        "StandardOutPath": str(args.data_dir / "launchd.out.log"),
        "StandardErrorPath": str(args.data_dir / "launchd.err.log"),
        "EnvironmentVariables": {
            "UV_CACHE_DIR": "/tmp/uv-cache",
            "PATH": os.environ.get(
                "PATH",
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            ),
        },
    }

    if args.dry_run:
        sys.stdout.buffer.write(plistlib.dumps(plist, sort_keys=False))
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)

    if args.load:
        subprocess.run(["launchctl", "load", str(path)], check=True)
    return path


def uninstall_launchd(args: argparse.Namespace) -> Path:
    path = launchd_plist_path(args.label)
    if args.unload and path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False)
    if path.exists():
        path.unlink()
    return path


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--data-dir", type=Path, default=default_state_dir())
    parser.add_argument("--archive-db", type=Path)
    parser.add_argument("--feed-settings-db", type=Path)
    parser.add_argument("--opml", dest="opml_path", type=Path)
    parser.add_argument("--feed-metadata", dest="feed_metadata_path", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive NetNewsWire articles into a long-term SQLite database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Merge the current NNW DB into the archive.")
    add_common_args(sync_parser)
    sync_parser.add_argument("--allow-schema-change", action="store_true")

    stats_parser = subparsers.add_parser("stats", help="Print archive summary as JSON.")
    add_common_args(stats_parser)

    web_parser = subparsers.add_parser("web", help="Run the local read-only search UI.")
    add_common_args(web_parser)
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    install_parser = subparsers.add_parser(
        "install-launchd", help="Install a daily sync LaunchAgent."
    )
    add_common_args(install_parser)
    install_parser.add_argument("--label", default=DEFAULT_LABEL)
    install_parser.add_argument("--hour", type=int, default=2)
    install_parser.add_argument("--minute", type=int, default=15)
    install_parser.add_argument("--allow-schema-change", action="store_true")
    install_parser.add_argument("--load", action="store_true")
    install_parser.add_argument("--dry-run", action="store_true")

    uninstall_parser = subparsers.add_parser(
        "uninstall-launchd", help="Remove the daily sync LaunchAgent."
    )
    uninstall_parser.add_argument("--label", default=DEFAULT_LABEL)
    uninstall_parser.add_argument("--unload", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "sync":
        result = sync_archive(
            source_db=args.source,
            data_dir=args.data_dir,
            archive_db=args.archive_db,
            feed_settings_db=args.feed_settings_db,
            opml_path=args.opml_path,
            feed_metadata_path=args.feed_metadata_path,
            allow_schema_change=args.allow_schema_change,
        )
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return 0

    if args.command == "stats":
        archive_db = resolve_archive_db(args.data_dir, args.archive_db)
        print(json.dumps(get_stats(archive_db), indent=2, ensure_ascii=False))
        return 0

    if args.command == "web":
        archive_db = resolve_archive_db(args.data_dir, args.archive_db)
        run_web(archive_db, host=args.host, port=args.port)
        return 0

    if args.command == "install-launchd":
        path = install_launchd(args)
        if not args.dry_run:
            print(f"Installed LaunchAgent: {path}")
        return 0

    if args.command == "uninstall-launchd":
        path = uninstall_launchd(args)
        print(f"Removed LaunchAgent: {path}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
