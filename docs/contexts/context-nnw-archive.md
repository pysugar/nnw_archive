# nnw_archive 项目上下文

> 最后更新：2026-04-30
> 稳定命令：`/opt/local/bin/nnw_archive`  
> 源码：`/opt/local/src/localtools/nnw_archive/`  
> 原型保留：`/opt/local/prototypes/localtools/rss/`

`nnw_archive` 是 NetNewsWire On My Mac 的长期历史归档工具。目标是只读读取
NetNewsWire 原生 SQLite 数据库，把文章、状态、作者和 feed 元数据合并到独立历史库，
避免 NetNewsWire 原生保留策略导致多年 feed 记录丢失。

## 1. 当前入口

稳定入口：

```bash
nnw_archive sync
nnw_archive stats
nnw_archive web --port 8765
nnw_archive install-launchd
nnw_archive uninstall-launchd
```

源码结构：

```text
src/localtools/nnw_archive/
├── __init__.py
├── cli.py      # click CLI，支持 bin wrapper 和直接执行 bootstrap
├── core.py     # SQLite schema、同步、搜索、feed 元数据合并
└── webapp.py   # Flask 只读搜索 UI 和 JSON API
```

`bin/nnw_archive` 是薄 wrapper：

```bash
source "$(cd "$(dirname "$0")" && pwd -P)/pyrunner.sh"
pyrun src/localtools/nnw_archive/cli.py "$@"
```

Prototype `prototypes/localtools/rss/nnw_archive.py` 先保留作为来源和对照；稳定开发应改
`src/localtools/nnw_archive/`，不要反向依赖 prototype。

## 2. 路径和依赖

默认源库查找顺序：

1. `~/Library/Containers/com.ranchero.NetNewsWire-Evergreen/Data/Library/Application Support/NetNewsWire/Accounts/OnMyMac/DB.sqlite3`
2. 当前工作目录的 `netnewswire.sqlite3`

可用 `--source /path/to/DB.sqlite3` 覆盖。当前本机 smoke test 使用的是
`prototypes/localtools/rss/netnewswire.sqlite3` symlink，因为原生容器路径在当前环境不存在。

默认状态目录：

```text
~/.local/state/netnewswire-archive/
```

重要文件：

- `archive.sqlite3` — 历史归档库
- `nnw_archive.log` — 命令和同步日志
- `launchd.out.log` / `launchd.err.log` — LaunchAgent 输出

依赖：

- `click` — CLI
- `flask` — Web UI
- Python 标准库 `sqlite3` backup API、`plistlib`、`xml.etree.ElementTree`

`pyproject.toml` 已声明 `click>=8.3.3` 和 `flask>=3.1.3`。

## 3. 同步语义

核心函数：`core.sync_archive()`。

- 只读打开 NetNewsWire 源库，并用 `sqlite3.Connection.backup()` 生成一致性临时快照。
- 历史库通过 `connect_archive()` 打开，启用 WAL、foreign_keys、busy_timeout。
- 首次同步记录源库核心 schema hash 和 schema payload；后续 hash 变化时默认中止，
  除非传 `--allow-schema-change`。
- schema 变化错误会输出旧/新 hash、变化级别和 unified diff。变化级别：
  `core table definitions changed` 表示核心 `CREATE TABLE` 定义变化；
  `auxiliary schema objects changed` 表示仅 index、trigger 或 view 等外围对象变化；
  `previous schema payload unavailable` 表示旧归档库还没有历史 payload，无法 diff。
- 每次同步全量读取当前快照，但只对 hash 变化的文章、状态、作者、feed 元数据写入。
- `read`、`starred`、`dateArrived` 使用最新 NetNewsWire 状态覆盖。
- 源库文章消失时不删除历史库记录，只设置 `archive_article_meta.source_deleted_at`。
- Feed 元数据由 `FeedSettings.db`、`Subscriptions.opml`、`FeedMetadata.plist` 合并，缺失或解析失败只记 warning，不阻断文章同步。
- 日志默认写入 `nnw_archive.log`；`-v/--verbose` 同时输出到 stderr。

## 4. 数据模型

原生镜像表：

- `articles`
- `statuses`
- `authors`
- `authorsLookup`

归档 sidecar 表：

- `archive_metadata` — archive schema version、当前源 schema hash 和当前 schema payload
- `archive_runs` — 每次同步统计、错误、源路径、schema hash
- `archive_source_schemas` — 按 hash 保存已接受过的源 schema payload、first/last seen 时间
- `archive_article_meta` — `created_at`、`updated_at`、`last_seen_at`、`source_deleted_at`、内容/状态 hash
- `archive_feeds` — 当前合并后的 feed 元数据
- `archive_feed_snapshots` — 每次同步的 feed 元数据快照
- `archive_search_docs` — FTS rowid 到 articleID 的稳定映射
- `archive_search` — FTS5 搜索表

时间策略：

- NetNewsWire 原生数值时间原样保留。
- `archive_*` sidecar 时间使用 UTC ISO-8601 字符串。
- plist 里读到的 datetime 会显式转为 Unix timestamp，避免 Python sqlite datetime adapter 警告。

## 5. Web/API

`webapp.py` 使用 Flask，默认只绑定 `127.0.0.1:8765`。

只读 API：

- `GET /api/stats`
- `GET /api/feeds`
- `GET /api/search?page=1&page_size=50&q=...`
- `GET /api/articles/<articleID>`

`/api/search` 返回：

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 0,
  "pages": 1
}
```

支持过滤：`q`、`feed_id`、`folder`、`starred`、`unread`、`from`、`to`。

## 6. 测试和验证

专项测试：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync -- python -m unittest tests/rss/test_nnw_archive.py
```

当前覆盖：

- 首次同步插入文章、状态、作者、feed 元数据
- 源 schema payload 记录、schema hash 变化 diff、`--allow-schema-change` 接受路径
- 同步幂等和 `last_seen_at`
- 最新状态覆盖
- 源库删除文章后历史保留并标记
- FTS 搜索
- Flask `/api/stats`、`/api/search` 分页、POST 405
- 默认源路径 fallback 到 `./netnewswire.sqlite3`
- click `stats --json`

真实数据 smoke test：

```bash
bin/nnw_archive sync --source prototypes/localtools/rss/netnewswire.sqlite3 --data-dir /tmp/nnw-archive-stable-smoke-20260426 --json
```

已验证当前真实库约 21106 篇文章、72 个 feed；连续同步最终文章、状态、feed 均为 0 更新。

## 7. 已知注意点

- `core.py` 仍保留从 prototype 复制来的旧 argparse/http.server 辅助代码；稳定入口已经走
  `cli.py` + `webapp.py`。后续可做一次无行为变化的清理。
- 全量 unittest discovery 需要按当前 `src/localtools` 布局处理；`bin/nnw_archive`
  已加入健康检查的 shell 文件列表。
