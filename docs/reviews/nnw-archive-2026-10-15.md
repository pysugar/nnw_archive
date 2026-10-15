# nnw_archive 功能审查与阅读界面优化

日期：2026-10-15。范围为稳定入口 `bin/nnw_archive` 对应的 `src/localtools/nnw_archive/`、
专项测试和说明文档。用户已验收展示效果并授权本地提交；交付限于本地代码和文档，
未推送、安装 LaunchAgent，也没有同步或回填正式数据库。
工作树原有的 gitool 修改保持原样。

## 已修复问题

| 问题 | 原因与修复 |
| --- | --- |
| 点击列表底部文章后正文空白 | Grid 子项默认最小尺寸撑高页面。固定视口，所有相关容器设置 `min-height: 0` 和 `minmax(0, 1fr)`，列表与正文分别滚动；新文章使用新的 iframe 从头显示。 |
| 快速搜索/切换文章时出现旧结果 | 旧请求可能后返回。新增请求取消和版本校验；错误、加载、空结果可见，并支持重试。 |
| 机器之心图片无法显示 | CDN 对无文章来源的请求返回 403；文章已有的微信 `data-src` 可正常访问。新增有限次数的原地址/备用地址回退，兼容相对 URL、懒加载和 srcset-only 图片。 |
| RSS CDATA 与站点样式影响正文 | 阅读渲染移除外层 CDATA，并通过元素/属性白名单清理正文；统一正文、代码、表格、图片样式。只在显示层处理，原始归档 HTML 不变。 |
| Web 统计会写入库 | 原 `get_stats()` 每次调用初始化 schema 并更新 metadata。全部读取函数改为 `mode=ro`，Web 启动不初始化数据库。 |
| 未读/星标数量与文章列表不一致 | 原统计直接计数 statuses，包含无文章的孤立状态。现在按真实文章关联统计，缺状态的文章与未读筛选保持一致。 |
| 结束日期漏掉当天文章 | 原 `to=YYYY-MM-DD` 只包含当天午夜。改为次日零点的排他上限；完整时间仍使用精确包含上限。 |
| 非法日期 500、越界分页空白 | 日期验证返回 JSON 400，页码收敛到实际末页。 |
| 作者变更后旧作者一直存在 | 删除仍在源库文章的失效作者关联；保留已移除文章及坏 JSON 对应的历史关联。 |
| 源库可能被误当作归档写入 | 初始化前拒绝同路径、符号链接、硬链接指向同一文件。 |
| 缺少 body 的 OPML 阻断同步 | 改为元数据 warning，继续文章同步。 |
| LaunchAgent 丢弃元数据参数 | 保留三个元数据覆盖路径，并校验小时和分钟范围。 |

## UI 行为

界面拆分为 Flask 模板、独立 CSS、应用交互脚本与正文辅助脚本，不增加前端框架和运行依赖。
统一中文文案、主题色、字号与留白，新增文章摘要、来源缩写、状态标识、原文入口、可收起的
文件夹/订阅源/日期筛选。深浅主题及字号可记忆；调整偏好保留阅读位置。

760px 以下切换为独立列表和阅读页，返回时恢复列表位置；320px 窄屏筛选不横向溢出。
界面仍是只读归档，阅读不会标记已读或更改星标。

正文 iframe 不允许 same-origin。文章脚本、事件处理器、样式和嵌套 frame 均清理；
CSP 仅允许随机 nonce 对应的自有 reader helper，未允许 unsafe-eval。helper 只用于图片回退、
字号和主题同步，并校验消息来源；未放开文章自带脚本。

## 验收证据

- 修复前复现：1440 × 900 窗口内，文档高 3607px；点击最后一项导致整页滚动 2707px，
  iframe 顶部坐标变成 −2565px。
- 修复后：文档高 900px，整页滚动为 0；列表内部可滚动超过 6000px，正文仍在可见区域，
  长文章滚到底后切换短文章正文回到 0，列表位置保持。
- 24 项专项单元测试通过。
- 浏览器 10 组场景通过：正文隔离、图片 403 回退/懒加载/srcset/失败提示、独立滚动、
  字号/主题保留位置、分页/组合状态筛选、文件夹/空结果、失败重试、乱序响应、移动端，
  且没有未捕获的浏览器错误。
- 真实归档通过 SQLite backup API 生成一致性副本，原库 mtime 不变；副本含 122196 篇文章、
  79 个订阅源，50 篇/页共 2444 页。一次本地测量：统计约 0.27 秒，首页搜索约 0.90 秒，
  单篇读取约 0.06 秒，仅为该次测量，不是性能承诺。
- 机器之心真实样本 `ede7e2b7f963df46a705fb56f1d3a85c`：修复前 3 张图 naturalWidth 均为 0；
  修复后均经微信原图返回 200，解码宽度分别为 845、791、1054px。
- `git diff --check`、JavaScript 语法检查通过。
- 仓库健康检查的 shellcheck、shfmt、Python 编译通过；608 项 unittest 中有 1 项无关失败：
  `tests.chat_export.test_export_chatgpt.ChatGPTExportCoreTests.test_html_goldens`，
  `assistant_code` 的代码块后空行差异。已独立复现；相关源文件、测试和依赖清单没有本次差异。

可重复的专项命令：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python -m unittest tests/rss/test_nnw_archive.py
UV_CACHE_DIR=/tmp/uv-cache uv run --no-project /opt/local/.venv/bin/python tests/rss/nnw_archive_browser_check.py --output-dir /tmp/nnw-archive-browser-check
node --check src/localtools/nnw_archive/static/app.js
node --check src/localtools/nnw_archive/static/reader.js
```

本次临时验收文件位于 `/tmp/nnw-archive-review/`，浏览器截图与结构化结果位于其
`verified/` 子目录。只读副本预览使用 `http://127.0.0.1:18766/`，验收后停止临时预览进程；
原有 8765 服务没有重启。
临时文件不是长期交付存储；上述脚本可重新生成样本和截图。

## 边界

- 图片回退只使用文章自身保存的地址，不猜测地址；若无备用地址且源站拒绝/失效，显示提示。
- 本次未对正式库执行 schema 变更、数据修复或历史 FTS 重建。同步语义修复在后续正常同步时生效。
- 既有 LaunchAgent 配置不自动改写；更新代码后，原 Web 进程需重新启动才能载入新版。
- `core.py` 内旧 prototype HTTP/argparse 代码暂保留，稳定入口仍是 `cli.py` + `webapp.py`。

## 本轮收束

- 用户已确认展示效果；本次范围内实现、专项验证、手册和上下文已完成。
- 仅提交 nnw_archive 源码、专项测试和本文档，不包含工作树原有 gitool 改动。
- 复用本轮已通过的 24 项专项测试和 10 组浏览器验收；实现未继续变更，不重复运行。
- 全仓库门禁唯一未通过项为上述 chat_export 既有快照差异，作为独立问题保留。
- 临时只读副本及截图保留在 `/tmp/nnw-archive-review/`，临时预览服务已收起。
- 本次无需继续编码；后续如需在原 8765 服务使用新版，应单独安排重启。远端推送、
  正式库写入和 LaunchAgent 重装均不属于本次授权范围。
