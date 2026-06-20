# AI 新闻速递（ai-news-express）工程宪法（Constitution / Steering）

> 项目的**长期原则与底线**——常驻、稳定、少变。AGENTS.md/CLAUDE.md 引用本文件。
> 借鉴 GitHub Spec Kit 的 constitution 与 Amazon Kiro 的 steering。

## 核心原则
1. **规范优先**：每个 feature 先有 spec（含可验证的验收标准）再编码。
2. **设计先行**：改业务源码前必须有 design.md（设计门强制）。
3. **测试是完成的一部分**：未通过测试的功能不算完成（收工门）。当前项目暂无测试框架，`tests_required` 默认关闭；接入 pytest 后应重新打开。
4. **决策可追溯**：不可逆/重大技术决策必须写 ADR（只追加）。
5. **提交规范**：约定式提交（提交门强制）。
6. **文档即时沉淀**：功能完成同时更新文档（收工门提醒）。

## 技术约束
- 语言/框架：Python 3.12；feedparser + openai SDK + jinja2 + pyyaml。
- 运行方式：GitHub Actions（每小时抓取 + 每日汇总），产物为静态网页部署到 GitHub Pages。
- 不允许：密钥写入仓库（`.env` 已 gitignore）；引入未评审的三方依赖。
- 安全底线：API key 仅经环境变量注入；外部抓取内容需做容错与校验。

## 质量门槛
- 测试命令：见 `.claude/devflow.config.json` 的 `test_cmd`（当前为空，待接入 pytest）。
- Lint 命令：`ruff check src`（需先 `pip install ruff`）。
- 评审：重要变更合并前建议跑 `/code-review`、`/security-review`。

## 例外
- fix/chore 类小改动走精简流程（在 feature 的 state.yml 关闭对应 gate）。
- 无 active feature 的零散杂活为 ad-hoc 模式，仅软提醒。
- 自动化产出的数据/网页（`data/`、`output/`，由 CI 定时提交）不走 feature 流程。
