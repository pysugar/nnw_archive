# nnw_archive

独立项目：`/opt/foundry/projects/pysugar/nnw_archive`。源码、测试和文档在本项目维护；
本机 `/opt/local/bin/nnw_archive` 保留为转发入口。安装和开发入口见[README](../../README.md)。

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

界面使用中文，左侧是搜索和文章列表，右侧是独立滚动的阅读区。列表滚动到底部后
点击文章，正文仍从开头显示；切换文章不会改变列表位置。手机和窄窗口下点击文章进入
阅读页，左上角返回按钮回到原来的列表位置。

- 搜索框右侧的筛选按钮可展开文件夹、订阅源和日期范围。
- 未读与星标可以同时开启；点击「清除筛选」恢复完整列表。
- 日期范围按 UTC 计算，结束日期包含当天所有文章；无效范围会提示错误。
- 右上角切换深浅主题；阅读工具栏的 `A−` / `A+` 调整字号，偏好会保存在当前浏览器。
- 点击「阅读原文」打开来源页面；在这里阅读不会改变 NetNewsWire 中的未读或星标。
- 图片地址加载失败时，会尝试文章中保存的备用图片地址。机器之心等订阅保存的
  `data-src` 原图可以自动回退；全部地址失效时显示提示，可前往原文查看。
- 加载失败可点击「重试」；筛选无结果时可以直接清除条件。

Web 和 `stats` 使用 SQLite 只读连接，不再隐式创建归档库。首次使用先运行
`nnw_archive sync`。更新代码后，需要重新启动当前 Web 进程，再刷新页面。

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

`--feed-settings-db`、`--opml`、`--feed-metadata` 会写入生成的同步命令，
`--hour` 范围为 0–23，`--minute` 为 0–59。更新代码不会自动修改已安装的 plist。

新 plist 使用当前 Python 解释器和 CLI 文件的绝对路径，不依赖 shell 的工作目录或旧仓库。
checkout 或解释器迁移后需重新生成。既有 `/opt/local/bin/nnw_archive` plist 可继续走本机
转发入口；此次独立迁移没有重装、加载或重启 LaunchAgent。

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
- 源库和归档不能是同一文件（包括符号链接、硬链接），避免误写 NetNewsWire 原库。
- 仍在源库中的文章会更新作者关联；源库已移除的文章保留作者信息。坏作者 JSON 不清空旧关联。

## 日志

默认日志：

```text
~/.local/state/netnewswire-archive/nnw_archive.log
```

所有命令支持 `-v/--verbose`，会同时把详细日志输出到 stderr。

常见排查：

- `NetNewsWire source database not found`：确认原生容器路径是否存在，或传
  `--source /path/to/DB.sqlite3` / 旧备份路径。
- `Source schema hash changed`：阅读错误里的变化级别和 schema diff。核心表定义变化
  要重点确认；仅 index/trigger/view 变化通常可接受。确认无误后用
  `--allow-schema-change` 记录新 schema。
- Feed 名称或 folder 缺失：同步不会因此失败，检查日志中 `FeedSettings.db`、OPML 或
  plist 的 warning。

## 验证

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python scripts/check.sh
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python tests/nnw_archive_browser_check.py --output-dir /tmp/nnw-archive-browser-check
```

浏览器脚本使用临时样本和独立 headless Chrome，验证滚动、筛选、图片、错误恢复与移动端，
不会连接真实归档或用户的浏览器 profile。默认使用已安装的 Chrome（`--channel chrome`）。
验收截图和 `results.json` 保存在 `--output-dir`。

macOS 上通过 Codex 执行浏览器脚本时，须从首次启动起使用经批准的沙箱外命令，
不能在默认沙箱中尝试启动 Chrome/Chromium；headless 同样适用。普通检查脚本不启动浏览器。

以下为旧 `/opt/local` 仓库 2026-04 的历史命令，不是新 checkout 的验收命令；
原数据库 symlink 未迁入项目。执行时写入的是指定临时目录：

```bash
bin/nnw_archive sync --source prototypes/localtools/rss/netnewswire.sqlite3 --data-dir /tmp/nnw-archive-stable-smoke-20260426 --json
```
