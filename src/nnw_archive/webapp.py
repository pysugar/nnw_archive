"""Flask web UI for the NetNewsWire archive."""

from __future__ import annotations

from pathlib import Path
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from . import core


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
            return jsonify({"error": exc.description}), exc.code
        if isinstance(exc, ValueError):
            return jsonify({"error": str(exc)}), 400
        core.logger.exception("Web request failed")
        return jsonify({"error": str(exc)}), 500

    @app.route("/")
    def index():
        return render_template("index.html")

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
    if not archive_db.is_file():
        raise FileNotFoundError("Archive database not found. Run nnw_archive sync first.")
    app = create_app(archive_db)
    app.run(host=host, port=port, debug=debug)
