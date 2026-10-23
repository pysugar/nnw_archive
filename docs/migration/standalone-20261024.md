# nnw_archive 独立迁移记录

日期：2026-10-24。目标是让 nnw_archive 独立维护和验证，保留原命令及归档数据位置。

## 来源与历史

- 原仓库：`/opt/local`，迁出前 HEAD：`bf3b8fc9f184be17db8f38961f30afed2bed643b`，工作树干净。
- 新仓库：`/opt/foundry/projects/pysugar/nnw_archive`，默认分支 `main`。
- Remote：`git@z.github.com:pysugar/nnw_archive.git`；`main` 已首次推送并设置跟踪 `origin/main`。
- 从本地独立 clone 中用 git-filter-repo 按路径提取 7 项相关提交，不改写原仓库。
- 最新历史提交：`3497ed067f95634223625bdf2132b45ea9fd183f` → `ecc226b`。
- [提交映射](commit-map.txt) 保留原始与新 SHA；[文件清单](source-manifest.tsv) 记录迁出前 SHA256。
  移除旧受控副本之前，逐一核对了 16 个文件与新仓库提取提交中的字节一致性。

## 迁移提交与推送状态

2026-10-24，两个仓库的迁移提交均已推送至各自 `origin/main`：

| 仓库 | 迁移提交 | Remote |
| --- | --- | --- |
| nnw_archive | [`e0e9767af0290c69e68d44c9886e86423d10237a`](https://github.com/pysugar/nnw_archive/commit/e0e9767af0290c69e68d44c9886e86423d10237a) | `git@z.github.com:pysugar/nnw_archive.git` |
| /opt/local | [`e2f67afd7f0eddfbc133a94d07456f8220d11d8b`](https://github.com/muchenzhan/optlocal/commit/e2f67afd7f0eddfbc133a94d07456f8220d11d8b) | `git@m.github.com:muchenzhan/optlocal.git` |

推送验收时，`git ls-remote origin refs/heads/main` 返回上述提交号，两边本地与远端
提交一致，工作树干净。以上是迁移提交号，后续文档提交不改变这两个迁移基线。

## 路径与维护归属

| 原路径（相对 /opt/local） | 新路径（相对本仓库） |
| --- | --- |
| `src/localtools/nnw_archive/` | `src/nnw_archive/` |
| `tests/rss/` | `tests/` |
| `prototypes/localtools/rss/` | `prototypes/rss/` |
| `docs/manuals/nnw_archive.md` | 同路径 |
| `docs/contexts/context-nnw-archive.md` | 同路径 |
| `docs/reviews/nnw-archive-2026-10-15.md` | 同路径，保留历史记录 |
| `bin/nnw_archive` | 独立 checkout 入口；旧路径改为本机转发 |

新增 `pyproject.toml`、`uv.lock`、`README.md`、`AGENTS.md`、`.gitignore` 和 `scripts/check.sh`。
运行依赖只有 Click、Flask 及其传递依赖；Playwright 是可选浏览器测试依赖。
锁文件中的本机已安装依赖版本与现有环境对齐，不借迁移升级运行依赖。

新入口通过 uv 使用 `NNW_ARCHIVE_PYTHON`，默认 `python3`，保留调用目录，且不做依赖同步。
本机转发入口默认选择 `/opt/local/.venv/bin/python`，支持 `NNW_ARCHIVE_ROOT`、
`NNW_ARCHIVE_PYTHON` 及旧 `PYRUNNER_PYTHON` 回退。
独立包提供 console script，并打包 Flask 模板、CSS 和 JavaScript。

新生成的 LaunchAgent 直接使用当前解释器和 CLI 文件的绝对路径，解决旧源码层级假设。
本次没有改写既有 plist；原计划仍通过保留的本机入口运行。
迁移前只读进程检查未发现匹配的 nnw_archive Web/CLI 进程，没有执行服务重启。

## 数据与环境边界

- `~/.local/state/netnewswire-archive/` 原位保留；未同步、回填或改写真实源库/归档库。
- 原型目录的 `netnewswire.sqlite3` 本机 symlink 原位保留，不作为新项目依赖，不纳入 Git。
- 只使用临时合成数据库验证。没有新建持久 venv，没有重新同步共享环境。
- 共享 Click/Flask/Playwright 仍被其他工具使用，原依赖声明保留；旧仓库只移除 nnw_archive lint 路径。
- 核心归档逻辑、Flask 路由、模板和前端资源保持字节不变。
- 两个仓库的迁移提交已推送；未安装/重启服务，未切换其他机器。

## 验证

- 迁移前 24 项专项测试通过。
- 独立仓库 27 项测试通过：新增 checkout 工作目录/解释器、缺失解释器、生成的 LaunchAgent 命令执行验证。
- ShellCheck、shfmt、Python 编译、JavaScript 语法与 CLI help 检查通过。
- wheel 和 sdist 构建通过；在 `/tmp` 中安装 wheel，验证 console script、首页模板、三项静态资源和四项只读 API。
- 所有业务验证使用临时数据库；测试重复同步幂等，源库 SHA256 保持一致。
- 浏览器脚本随项目迁移并通过编译；本次未重跑浏览器场景，前端文件未改动。
- 原仓库完整健康检查：ShellCheck、shfmt 和 Python 编译通过；513 项 unittest 中 512 通过，
  1 项为已有的 `tests.chat_export.test_export_chatgpt.ChatGPTExportCoreTests.test_html_goldens`
  `assistant_code` 代码块后空行差异。相关实现、fixture 和测试没有本次改动；同一失败已记录在
  2026-10-15 审查中。本次未改动无关 exporter。
- 旧转发入口 `--help` 已通过，两个仓库 `git diff --check` 通过。

复现独立检查：

```bash
NNW_ARCHIVE_PYTHON=/opt/local/.venv/bin/python scripts/check.sh
```

## 回滚与后续

先恢复旧仓库迁出前基线中的入口、受控文件和相应索引，再恢复旧命令调用。
原始 Git 历史完整保留；无需回滚数据库。保留新仓库和迁移证据供比较。
本机迁移及两个仓库的 GitHub 推送已完成；其他机器切换和服务重装需另行安排。
切换其他机器前先准备新 checkout 或安装独立包，避免转发入口找不到项目。
