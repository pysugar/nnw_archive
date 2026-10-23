# nnw_archive

Independent NetNewsWire archive CLI and read-only Flask UI. Read
`docs/contexts/context-nnw-archive.md` and `docs/manuals/nnw_archive.md` before behavior changes.

- Reply in Simplified Chinese unless requested otherwise. Write code comments in English.
- Use uv. On this Mac run `uv run --no-project /opt/local/.venv/bin/python` and reuse that environment.
  Do not sync this project into the shared environment or create persistent environments without authorization.
- Run `NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python scripts/check.sh` locally.
  Elsewhere, use an authorized isolated environment and `uv sync --locked`, then `uv run --no-sync scripts/check.sh`.
- Tests must use temporary fixtures or explicit copies. Never sync, migrate or rewrite live databases for verification.
  Default data stays in `~/.local/state/netnewswire-archive/`; source databases are read-only.
- Keep browser tests optional. Do not launch Chrome/Chromium from a sandboxed macOS shell:
  use an approved escalated launch or supported browser connection, with an isolated temporary profile.
- Do not commit databases, source database symlinks, logs, caches, backups or secrets.
- Keep `src/nnw_archive` independent of `/opt/local` and of `prototypes/rss`.
  The external `/opt/local/bin/nnw_archive` is a host forwarding entrypoint only.
- Make targeted changes and atomic English imperative commits. No push, deployment, service restart or live writes without authorization.
- Related history was extracted from `/opt/local`; see `docs/migration/commit-map.txt` and `docs/migration/standalone-20261024.md`.
