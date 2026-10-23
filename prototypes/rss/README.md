# NetNewsWire Archive Prototype

> 稳定版已经迁移到 `bin/nnw_archive` 和 `src/nnw_archive/`。本目录保留为
> prototype 来源和对照验证区；旧机器的数据库 symlink 未迁入；日常使用请优先运行
> `nnw_archive sync` / `nnw_archive web`。

`nnw_archive.py` 是 NetNewsWire 长期历史归档原型。它只读 NetNewsWire 的原生
SQLite 数据库，通过 SQLite backup API 取得一致快照，然后把文章、状态、作者和
feed 元数据合并到独立历史库。

历史使用的本地源库 symlink（未随项目迁移，验证时请显式指定临时源库）：

```bash
prototypes/rss/netnewswire.sqlite3
```

默认归档库：

```bash
~/.local/state/netnewswire-archive/archive.sqlite3
```

## Commands

稳定版：

```bash
nnw_archive sync
nnw_archive stats
nnw_archive web --port 8765
```

Prototype 直接执行仍可用于对照：

首次同步：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py sync
```

查看统计：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py stats
```

启动本地只读搜索页面：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py web --port 8765
```

导入旧备份或 Time Machine 里的旧 `DB.sqlite3`：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py sync --source /path/to/old/DB.sqlite3
```

如果旧库来自 NetNewsWire 不同 schema，先人工确认结构，再显式允许：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py sync --source /path/to/old/DB.sqlite3 --allow-schema-change
```

## Daily LaunchAgent

生成每日同步任务，默认 02:15 运行：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py install-launchd
```

仅预览 plist：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py install-launchd --dry-run
```

移除：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python prototypes/rss/nnw_archive.py uninstall-launchd
```

## Data Semantics

- 原生镜像表：`articles`、`statuses`、`authors`、`authorsLookup`。
- 归档 sidecar 表统一使用 `archive_` 前缀。
- `archive_article_meta.created_at` 是文章第一次进入归档库的 UTC 时间。
- `archive_article_meta.updated_at` 在文章内容或 read/starred/dateArrived 状态变化时更新。
- `archive_article_meta.last_seen_at` 每次源库仍存在该文章时更新。
- 如果文章从 NetNewsWire 当前库消失，不会从归档库删除，只会设置 `source_deleted_at`。
- `statuses` 使用最新状态覆盖，方便历史搜索时仍看到当前 read/starred 状态。
- feed 名称、folder、主页和图标来自 `FeedSettings.db`、`Subscriptions.opml` 和
  `FeedMetadata.plist` 的合并结果。

## Safety Notes

- 工具不会写入 NetNewsWire 的 `DB.sqlite3`、`FeedSettings.db`、OPML 或 plist。
- 同步前会通过 `sqlite3.Connection.backup()` 创建一致性临时快照，避免直接裸拷贝运行中的 SQLite 文件。
- 首次同步会记录源库核心 schema hash；后续 hash 变化时默认中止，除非传
  `--allow-schema-change`。
- 这个原型不尝试让 NetNewsWire 读取历史库，也不改动 NetNewsWire 原生保留策略。

## Validation

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python -m py_compile prototypes/rss/nnw_archive.py
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python -m unittest tests/test_nnw_archive.py
```
