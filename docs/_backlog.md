# Backlog — AI 新闻速递

> 待办、想法与技术债的集中地。feature 起步时从这里捞需求。

## 待办 / 想法
- （空）

## 技术债
- **缺测试框架**：项目无 `tests/` 目录、未接入 pytest。收工门的 `tests_required` 暂关闭，建议后续补单测并打开。
- **Lint 未接入**：`ruff` 未安装，baseline lint 扫描已跳过。`pip install ruff` 后可跑 `ruff check src` 建立基线。
