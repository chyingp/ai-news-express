# 后续跟进事项（TODO）

记录已知但暂未处理的事项，供后续迭代跟进。按优先级与主题分组。

---

## 数据源 · 统计页（/stats/）

### 1. 启用 FRED 获取月度时效（需用户操作）
- **现状**：统计页数据来自 World Bank，多为**年度**数据；CPI 等想要月度时效暂不可得。
- **动作**：去 https://fred.stlouisfed.org/docs/api/api_key.html 申请免费 API key，
  加到仓库 **Settings → Secrets and variables → Actions**，命名 `FRED_API_KEY`。
- **效果**：`stats_fetcher` 检测到该 key 后自动对带 `fred` 配置的指标改用月度序列；
  不加也能正常运行（回退 World Bank 年度）。

### 2. 校验 / 修正 FRED 中国月度序列编码
- **背景**：当前仅给 CPI 配了 FRED 序列 `CPALTT01CNM659N`（OECD MEI）。部分 OECD MEI
  序列在 FRED 上已于 2024 前后停更，可能 404/空。代码已按"可失败回退"处理，不会报错。
- **动作**：加上 `FRED_API_KEY` 后看 CI 日志 `[stats] FRED fetch failed ...`，
  对停更的序列换成仍在更新的等价序列，并给更多指标（工业增加值、社零等）补 `fred` 编码。
- **位置**：`src/stats_fetcher.py` 的 `INDICATORS` 注册表。

### 3. 补充 World Bank 缺失的指标（如房地产开发投资）
- **现状**：房地产开发投资、社零总额月度等 World Bank 无对应口径，暂未上页。
- **动作**：从 FRED 或其它公开源找替代序列后加入注册表；房地产可能需国家统计局原数。

### 4. （可选）国家统计局原始数据直连方案
- **背景**：`data.stats.gov.cn` 数据 API 在 GitHub Actions（海外 IP）被反爬 WAF 拦截
  （首页 200、easyquery 403），无法稳定直连，故改用 World Bank/FRED 转载数据。
- **动作（若需官方原数与时效）**：在中国境内部署一个定时任务（云函数/服务器）抓统计局，
  把 JSON 提交到本仓库（如 `data/stats/nbs.json`），统计页改为只读该文件。
  需用户提供境内运行环境。

---

## 信源稳定性

### 5. 复核微软官方博客源质量
- **背景**：原 `blogs.microsoft.com/ai/feed/` 返回 410（已下线），改用总站
  `blogs.microsoft.com/feed/` + `ai_filter` 过滤非 AI 内容。
- **动作**：观察若干运行，确认能稳定产出 AI 相关条目；若噪声多或抓不到，
  换更精准的微软 AI 源或停用（`enabled: false`）。位置：`config/sources.yaml`。

### 6. Nitter（X/Twitter 专家源）实例可用性
- **背景**：X 无官方 RSS，经 Nitter 桥接；公共 Nitter 实例常不稳定/失效。
- **动作**：若专家观点板块长期为空，切换 `config/sources.yaml` 顶层 `nitter_instance`
  到可用实例，或改用其它桥接方案。

---

## 基础设施

### 7. 升级 GitHub Actions 到 Node 24 兼容版本
- **背景**：CI 日志持续告警 `actions/checkout@v4`、`actions/setup-python@v5`、
  `actions/upload-artifact@v4` 仍跑在 Node 20，2026-09 后将被移除。
- **动作**：跟进各 action 的新版本（支持 Node 24）后升级 `.github/workflows/*.yml`。

---

## 已知取舍（记录，非必须改）

### 8. 企业标签回溯范围 = 首页展示窗口（最近 3 天）
- LLM「主体公司」判定只回溯最近 3 天的存档（与首页展示一致）；更早的历史文章
  保留关键词标签。因旧文章会随时间自然沉底，一般无需处理。
- 如需对全部历史回溯，可临时调大 `processor.backfill_companies(limit_files=N)`。

### 9. 统计页随 hourly 流程刷新
- 统计页未单独建工作流，而是并入 hourly/daily 末尾生成（当天已抓过则复用、不重复请求），
  由现有部署一并发布。逻辑简单、无并发部署竞争；如需独立调度再拆分。
