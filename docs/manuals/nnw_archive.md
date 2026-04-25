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

导入旧备份：

```bash
nnw_archive sync --source /path/to/old/DB.sqlite3
```

如果 NetNewsWire schema hash 变化，默认会中止同步；人工确认后可显式允许：

```bash
nnw_archive sync --allow-schema-change
```

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

- 原生镜像表：`articles`、`statuses`、`authors`、`authorsLookup`。
- 归档 sidecar 表统一使用 `archive_` 前缀。
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
