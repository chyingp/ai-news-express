# AGENTS.md — AI 新闻速递 AI 协作规范

> 跨工具 agent 指令（Claude Code / Copilot / Cursor / Gemini 通用）。
> 本项目启用 **devflow** 状态驱动 SDLC 流程（Claude Code 插件，hooks 自动门禁）。
> 长期原则见 [constitution.md](docs/constitution.md)。

## 研发生命周期

| 阶段 | 做什么 | 产物落哪 | 关卡 |
|---|---|---|---|
| ① 需求/Spec | 澄清意图、定验收标准（/devflow:feature-clarify）| `docs/specs/<f>/spec.md` | — |
| ② 设计/架构 | 技术方案、接口、数据模型 | `docs/specs/<f>/design.md` | 设计门(硬) |
| ③ 决策记录 | 记录不可逆/重大决策 | `docs/adr/NNNN-*.md`（只追加）| — |
| ④ 编码 | 实现 | 见 config.modules[].source_globs | — |
| ⑤ 测试 | 单测 + 验证 | 项目测试目录 | 收工门(硬) |
| ⑥ 评审+安全 | code-review / security-review | — | — |
| ⑦ 提交 | 约定式提交 | git | 提交门(硬) |
| ⑧ 文档沉淀 | 文档/知识库 | README / 知识库 | 收工门(硬) |

## 三道硬门禁（devflow hooks 自动执行）
1. **设计门**：改业务源码前需 `state.design=done`。
2. **提交门**：`git commit -m` 必须符合约定式提交。
3. **收工门**：动过代码后，结束前需 `tests=done`（本项目暂关）且 `docs=done`、验收标准已核对。

## 常用命令
- `/devflow:feature-start <name>`：起步新 feature（建 spec/design/state）
- `/devflow:feature-clarify`：对抗式澄清，补全验收标准
- `/devflow:feature-status`：查看当前阶段与待过关卡
- `/devflow:devflow-doctor`：体检配置与环境
- 测试 / Lint：见 `.claude/devflow.config.json`

## 任务分级
- feature 走全流程；fix/chore 精简（关闭对应 gate）；ad-hoc 杂活仅软提醒。
- `data/`、`output/` 由 CI 定时生成提交，不走 feature 流程。
