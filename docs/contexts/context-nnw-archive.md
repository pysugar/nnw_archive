# nnw_archive 项目上下文

> 最后更新：2026-10-24
> 独立项目：`/opt/foundry/projects/pysugar/nnw_archive`
>
> 稳定命令：`bin/nnw_archive`；本机 `/opt/local/bin/nnw_archive` 仅转发
>
> 源码：`src/nnw_archive/`；原型：`prototypes/rss/`
>
> Remote：`git@z.github.com:pysugar/nnw_archive.git`

`nnw_archive` 是 NetNewsWire On My Mac 的长期历史归档工具。目标是只读读取
NetNewsWire 原生 SQLite 数据库，把文章、状态、作者和 feed 元数据合并到独立历史库，
避免 NetNewsWire 原生保留策略导致多年 feed 记录丢失。

2026-10-24 独立迁移已完成，两个仓库的迁移提交均已推送至各自 `origin/main`：

- 本项目：[`e0e9767`](https://github.com/pysugar/nnw_archive/commit/e0e9767af0290c69e68d44c9886e86423d10237a)，独立维护、运行和验证入口。
- `/opt/local`：[`e2f67af`](https://github.com/muchenzhan/optlocal/commit/e2f67afd7f0eddfbc133a94d07456f8220d11d8b)，迁出实现并保留本机转发入口。

推送后已核对远端提交号；正式数据、已安装 LaunchAgent 和服务进程未因迁移而改写或重启。
完整提交号、验收和回滚边界见[迁移记录](../migration/standalone-20261024.md)。
后续维护从本独立仓库继续；第 8 节保留的是 2026-10-15 历史收束记录。

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
src/nnw_archive/
├── __init__.py
├── cli.py                  # Click entry point and LaunchAgent configuration
├── core.py                 # Archive sync, SQLite schema and queries
├── webapp.py               # Read-only Flask routes
├── templates/index.html    # Application shell
└── static/
    ├── style.css           # Responsive library and reader layout
    ├── app.js              # Search, selection and sanitized article documents
    └── reader.js           # Sandboxed image fallback and reading preferences
```

`bin/nnw_archive` 通过 uv 调用所选解释器，保留调用目录和参数，不同步依赖。
解释器由 `NNW_ARCHIVE_PYTHON` 指定，默认 `python3`。本机使用：

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python bin/nnw_archive --help
```

Prototype `prototypes/rss/nnw_archive.py` 保留作为来源和对照；稳定开发应改
`src/nnw_archive/`，不要反向依赖 prototype。安装包提供 `nnw_archive` console script。
新生成的 LaunchAgent 直接绑定当前解释器和 CLI 文件路径；搬动 checkout 或环境后需重新生成。
既有 plist 仍可通过 `/opt/local/bin/nnw_archive` 转发；本次未重装或加载服务。

## 2. 路径和依赖

默认源库查找顺序：

1. `~/Library/Containers/com.ranchero.NetNewsWire-Evergreen/Data/Library/Application Support/NetNewsWire/Accounts/OnMyMac/DB.sqlite3`
2. 当前工作目录的 `netnewswire.sqlite3`

可用 `--source /path/to/DB.sqlite3` 覆盖。2026-04 的 smoke test 使用过
`prototypes/localtools/rss/netnewswire.sqlite3` symlink；不要据此推断当前机器的原生源库位置。

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
- 在初始化归档表之前拒绝源库与归档指向同一文件，包括符号链接和硬链接。
- 历史库通过 `connect_archive()` 打开，启用 WAL、foreign_keys、busy_timeout。
- 首次同步记录源库核心 schema hash 和 schema payload；后续 hash 变化时默认中止，
  除非传 `--allow-schema-change`。
- schema 变化错误会输出旧/新 hash、变化级别和 unified diff。变化级别：
  `core table definitions changed` 表示核心 `CREATE TABLE` 定义变化；
  `auxiliary schema objects changed` 表示仅 index、trigger 或 view 等外围对象变化；
  `previous schema payload unavailable` 表示旧归档库还没有历史 payload，无法 diff。
- 每次同步全量读取当前快照，但只对 hash 变化的文章、状态、作者、feed 元数据写入。
  作者来源兼容旧版 `authors` / `authorsLookup` 表和新版 `articles.authors` JSON 字段。
- 对仍在源库中的文章清理失效的作者关联；已保留的历史文章不受影响。
  坏 JSON / 非列表作者数据保留旧关联，有效的空作者列表则清空关联。
- `read`、`starred`、`dateArrived` 使用最新 NetNewsWire 状态覆盖。
- 源库文章消失时不删除历史库记录，只设置 `archive_article_meta.source_deleted_at`。
- Feed 元数据由 `FeedSettings.db`、`Subscriptions.opml`、`FeedMetadata.plist` 合并，缺失或解析失败只记 warning，不阻断文章同步。
- 日志默认写入 `nnw_archive.log`；`-v/--verbose` 同时输出到 stderr。

## 4. 数据模型

归档库中的原生镜像表：

- `articles`
- `statuses`
- `authors` — 源库旧版作者表或新版 `articles.authors` JSON 标准化后的作者记录
- `authorsLookup` — 源库旧版作者关联表或新版 JSON 派生出的文章-作者关联

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
`stats` 和全部 Web 查询通过 SQLite `mode=ro` 打开归档，不再执行建表或 schema 写入。
首次使用需先运行 `sync` 创建归档；`web` 不再隐式初始化空库。

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
`to=YYYY-MM-DD` 包含整个 UTC 日期；时间戳或完整 ISO 时间仍按精确上限包含。
无效日期、非有限时间戳和反向范围返回 JSON 400。越界页码收敛到实际末页。
文章列表额外提供去除 HTML 的 `excerpt`，不返回临时截取字段。

当前 UI：

- 桌面固定视口两栏：列表、阅读正文独立滚动；切换文章重置正文，保留列表位置。
- 760px 及以下显示独立列表/阅读页；返回列表保留位置。
- 中文界面、摘要、来源/日期/状态、原文入口、文件夹/订阅源/日期筛选。
- 未读和星标可组合；筛选、翻页清空旧文章选择，列表滚动回到顶部。
- 请求使用 `AbortController` 与版本号，防止旧搜索/旧文章响应覆盖新选择。
- 加载、无结果、失败重试、当前选中状态；订阅源失败不阻断搜索。
- 深浅主题和 15–23px 正文字号保存在 localStorage；偏好变化不重置阅读进度。
- `/` 聚焦搜索；窄屏主页面按 `Escape` 或点击返回按钮回到列表。

正文与图片：

- 正文先移除外层 RSS CDATA，再按 HTML 元素/属性白名单生成阅读文档。
- 相对图片/链接基于原文章 URL 解析；支持 `data-src`、`data-original`、
  `data-lazy-src`、`data-original-src`、`data-canonical-src` 和只有 srcset 的图片。
- 正常图片失败后仅尝试文章已有的备用地址（最多 8 个），没有可用图片时显示提示和图片链接。
- 机器之心已实测：`image.jiqizhixin.com` 无来源请求返回 403，保存的微信 `data-src`
  可直接访问；不增加图片代理或改写归档数据。
- iframe 保持 opaque origin（不允许 `allow-same-origin`），仅允许带随机 CSP nonce 的
  自有 `reader.js` 处理图片和偏好。文章脚本、事件属性、外部样式、嵌套 iframe 被移除；
  CSP 不允许其他脚本和动态 eval。父子窗口通信校验来源窗口，子窗口同时校验父页面 origin。

## 6. 测试和验证

专项测试：

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python scripts/check.sh

UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python tests/nnw_archive_browser_check.py --output-dir /tmp/nnw-archive-browser-check
```

当前覆盖：

- 首次同步插入文章、状态、作者、feed 元数据
- 新版 NetNewsWire `articles.authors` JSON 作者兼容，以及无作者/坏 JSON 的容错路径
- 源 schema payload 记录、schema hash 变化 diff、`--allow-schema-change` 接受路径
- 同步幂等和 `last_seen_at`
- 最新状态覆盖
- 源库删除文章后历史保留并标记
- FTS 搜索
- Flask `/api/stats`、`/api/search` 分页、POST 405
- 默认源路径 fallback 到 `./netnewswire.sqlite3`
- click `stats --json`
- 真正只读查询、孤立状态统计、整日日期上限、错误输入和分页越界
- 相同源库/归档拒绝、畸形 OPML 容错、作者关联替换与历史保留
- LaunchAgent 元数据参数保留、小时/分钟范围检查

2026-10-15：24 项专项单元测试通过；浏览器脚本覆盖 10 组场景，包括桌面、深色、
390px / 320px 窄屏、滚动回归、乱序响应、错误重试和图片回退。
脚本创建临时数据库和临时 HTTP 服务，使用独立 headless Chrome；macOS 上须经批准在沙箱外启动，
不得先从默认沙箱尝试。外部图片请求在测试中模拟，
不读取正式归档、不操作用户的 Chrome profile。截图和 `results.json` 写入指定目录。

以下是迁移前 2026-10-15 的历史记录，不是当前独立仓库门禁结果。
原仓库门禁 `scripts/healthz/check_localtools.sh`：shellcheck、shfmt、Python 编译通过；
608 项单元测试中有 1 项既有的无关失败：
`tests.chat_export.test_export_chatgpt.ChatGPTExportCoreTests.test_html_goldens` 的
`assistant_code` 样本在代码块后少一个空行。该失败独立复现，本次未修改相关模块。

真实数据 smoke test：

```bash
bin/nnw_archive sync --source prototypes/localtools/rss/netnewswire.sqlite3 --data-dir /tmp/nnw-archive-stable-smoke-20260426 --json
```

上述为 2026-04 的历史同步验证：约 21106 篇文章、72 个 feed；连续同步最终文章、状态、
feed 均为 0 更新。2026-10-15 使用现有归档的一致性副本，只验证读取：122196 篇文章、
79 个 feed、2444 页（每页 50 篇）。没有对正式库执行同步或回填。

本次检查和修复明细：[2026-10-15 review](../reviews/nnw-archive-2026-10-15.md)。

## 7. 已知注意点

- `core.py` 仍保留从 prototype 复制来的旧 argparse/http.server 辅助代码；稳定入口已经走
  `cli.py` + `webapp.py`。后续可做一次无行为变化的清理。
- 独立仓库测试使用 `tests/` 与 `src/`；旧 localtools 门禁只检查本机转发脚本。
- 图片仍依赖源站可访问性；没有备用地址且所有来源均拒绝访问/失效时，显示失败提示。
  原样保存的文章 HTML 与历史 FTS 未在本次验收中执行批量改写或重建。
- `install-launchd` 现在保留三个元数据覆盖参数并校验时刻；已有安装的 plist 不会自动改写。

## 8. 2026-10-15 收束状态

以下为历史记录。当前迁移状态、验证结果和边界见
[2026-10-24 独立迁移](../migration/standalone-20261024.md)。

- 用户已验收新版 UI 和图片展示效果，并授权将本轮 nnw_archive 改动提交到本地。
- 本轮功能、专项测试、浏览器验收与文档已完成，无待继续实现项；全仓库检查的
  chat_export 既有失败保留为独立问题。
- 临时 18766 只读副本预览已停止，副本与验收截图仍保留在 `/tmp/nnw-archive-review/`。
- 原有 8765 进程未重启；正式归档没有执行同步、回填或 FTS 重建，已有 LaunchAgent 未改写。
- gitool 的原有工作树改动不在本次提交中。没有推送远端；后续发布或服务切换需另行授权。
