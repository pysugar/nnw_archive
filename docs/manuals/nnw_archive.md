# nnw_archive

NetNewsWire On My Mac 长期历史归档工具。它只读 NetNewsWire 原生 SQLite 数据库，
通过 SQLite backup API 取得一致快照，再合并到独立历史库：

```text
~/.local/state/netnewswire-archive/archive.sqlite3
```

默认源库查找顺序：

1. `~/Library/Containers/com.ranchero.NetNewsWire-Evergreen/Data/Library/Application Support/NetNewsWire/Accounts/OnMyMac/DB.sqlite3`
2. 当前工作目录的 `netnewswire.sqlite3`

可用 `--source /path/to/DB.sqlite3` 覆盖。

## 常用命令

```bash
nnw_archive sync
nnw_archive stats
nnw_archive web --port 8765
```

查看完整 JSON 同步结果：

```bash
nnw_archive sync --json
```

导入旧备份：

```bash
nnw_archive sync --source /path/to/old/DB.sqlite3
```

如果 NetNewsWire schema hash 变化，默认会中止同步；人工确认后可显式允许：

```bash
nnw_archive sync --allow-schema-change
```

同步中止时，错误信息会列出旧/新 schema hash、变化级别，并在已有历史 payload
时输出 unified diff。变化级别分为：

- `core table definitions changed`：`articles`、`statuses`，以及源库中仍存在的旧版
  `authors` / `authorsLookup` 的 `CREATE TABLE` 定义变化，需要重点确认字段语义。
- `auxiliary schema objects changed`：核心表定义未变，只是相关 index、trigger 或 view
  变化，通常风险较低。
- `previous schema payload unavailable`：旧归档库还没有保存 schema payload，无法自动
  diff；确认后运行一次 `--allow-schema-change`，后续变化会有 diff。

## Web UI

```bash
nnw_archive web --host 127.0.0.1 --port 8765
```

Web UI 基于 Flask，只提供只读接口：

- `GET /api/search?page=1&page_size=50&q=...`
- `GET /api/articles/<articleID>`
- `GET /api/feeds`
- `GET /api/stats`

搜索支持全文搜索、feed、folder、starred、unread 和时间过滤；返回结果带
`page`、`page_size`、`total`、`pages` 和 `items`。

分页示例：

```bash
curl 'http://127.0.0.1:8765/api/search?q=agent&page=2&page_size=25'
```

## 每日同步

安装 LaunchAgent：

```bash
nnw_archive install-launchd
```

预览 plist：

```bash
nnw_archive install-launchd --dry-run
```

移除：

```bash
nnw_archive uninstall-launchd
```

## 数据语义

- 归档库保留原生镜像表：`articles`、`statuses`、`authors`、`authorsLookup`。
  新版 NetNewsWire 源库可能不再有 `authors` / `authorsLookup` 表；同步会从
  `articles.authors` JSON 字段标准化出作者和文章-作者关联。
- 归档 sidecar 表统一使用 `archive_` 前缀。
- `archive_source_schemas` 按 hash 保存已接受过的 NetNewsWire 源库 schema payload，
  用于后续 schema 变化 diff。
- `created_at` 表示文章第一次进入归档库的 UTC 时间。
- `updated_at` 在文章内容或 read/starred/dateArrived 状态变化时更新。
- `last_seen_at` 每次源库仍存在该文章时更新。
- 源库删除文章时归档库不删除，只写 `source_deleted_at`。
- read/starred/dateArrived 使用最新 NetNewsWire 状态覆盖。

## 日志

默认日志：

```text
~/.local/state/netnewswire-archive/nnw_archive.log
```

所有命令支持 `-v/--verbose`，会同时把详细日志输出到 stderr。

常见排查：

- `NetNewsWire source database not found`：确认原生容器路径是否存在，或传
  `--source prototypes/localtools/rss/netnewswire.sqlite3` / 旧备份路径。
- `Source schema hash changed`：阅读错误里的变化级别和 schema diff。核心表定义变化
  要重点确认；仅 index/trigger/view 变化通常可接受。确认无误后用
  `--allow-schema-change` 记录新 schema。
- Feed 名称或 folder 缺失：同步不会因此失败，检查日志中 `FeedSettings.db`、OPML 或
  plist 的 warning。

## 验证

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync -- python -m unittest tests/rss/test_nnw_archive.py
UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync -- python -m py_compile src/localtools/nnw_archive/core.py src/localtools/nnw_archive/cli.py src/localtools/nnw_archive/webapp.py
```

当前真实数据 smoke test 使用：

```bash
bin/nnw_archive sync --source prototypes/localtools/rss/netnewswire.sqlite3 --data-dir /tmp/nnw-archive-stable-smoke-20260426 --json
```
