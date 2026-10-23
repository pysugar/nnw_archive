# nnw_archive

NetNewsWire On My Mac 长期历史归档工具。只读读取 NetNewsWire 源库，增量保存文章、
作者、状态和订阅元数据，并提供全文搜索与只读阅读界面。源库删除文章后，历史归档仍保留。

项目于 2026-10-24 从 localtools 独立，源码、测试和文档在本仓库维护。
Git remote：`git@z.github.com:pysugar/nnw_archive.git`。

## 使用

需要 Python 3.13+、Click 和 Flask。浏览器回归额外依赖 Playwright，日常使用不需要它。

在已经配置好依赖的环境中，可直接使用 checkout 入口：

```bash
NNW_ARCHIVE_PYTHON=/path/to/python bin/nnw_archive --help
```

本机复用现有共享环境，不执行 `uv sync`：

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python bin/nnw_archive --help
```

`/opt/local/bin/nnw_archive` 保留为本机转发入口，可继续使用原命令。
其他机器在已批准的独立开发环境中可运行 `uv sync --locked`，再使用
`uv run --no-sync nnw_archive --help`。安装包也提供同名 console script。

```bash
nnw_archive sync
nnw_archive stats
nnw_archive web --host 127.0.0.1 --port 8765
nnw_archive install-launchd --dry-run
```

`sync` 会写入历史归档；首次使用先同步，再启动 Web。默认状态目录始终是
`~/.local/state/netnewswire-archive/`，独立迁移不会搬动数据库。
Web 和 `stats` 只读查询归档；CLI 日志写入状态目录。

## 开发与验证

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python scripts/check.sh
```

检查包含 Shell 格式与静态检查、Python 编译和临时数据库测试、JavaScript 语法与入口检查。
构建包：`uv build --python /opt/local/.venv/bin/python`；构建不重新同步共享环境。
浏览器验收命令及 macOS 启动限制见[手册](docs/manuals/nnw_archive.md#验证)。

```text
bin/nnw_archive               checkout CLI
src/nnw_archive/              CLI、归档核心、Flask、模板与静态资源
tests/                       单元测试、入口测试与可选浏览器回归
scripts/check.sh             独立质量检查
docs/manuals/                使用手册
docs/contexts/               工程上下文
docs/reviews/                历史验收
docs/migration/              迁移记录与 Git 提交映射
prototypes/rss/              历史原型，不作为运行依赖
```

- [完整手册](docs/manuals/nnw_archive.md)
- [项目上下文](docs/contexts/context-nnw-archive.md)
- [迁移范围、验证与回滚](docs/migration/standalone-20261024.md)
- [阅读界面与数据语义审查](docs/reviews/nnw-archive-2026-10-15.md)
