# AI 新闻速递 - 设计文档

## Context

作为一名在中国大陆的 AI 技术专家，需要一个自动化系统来高效获取国内外第一手 AI 资讯。当前通过公众号和群消息获取的信息存在滞后、不准确、夸大等问题。本项目旨在建设一个自动化 AI 资讯聚合系统，定时从高质量信源抓取资讯，通过 Claude API 进行智能处理（摘要、分类、重要性评分），生成简洁美观的静态网页，部署到 GitHub Pages。

## 技术选型

| 维度 | 选择 | 说明 |
|------|------|------|
| 推送/阅读渠道 | 静态网页（GitHub Pages） | 免费托管，全球可访问 |
| LLM | Claude API（Sonnet 4.6） | 性价比最优，支持结构化 JSON 输出 |
| 技术栈 | Python 3.12 | feedparser + anthropic SDK + jinja2 |
| 运行方式 | GitHub Actions | 每小时检查 + 每日 08:00/20:00 汇总 |
| 信源范围 | 14 个活跃信源 | 覆盖国内外主流 AI 资讯来源 |
| 展示风格 | 简洁卡片流（类 Hacker News） | 深色/浅色模式，响应式布局 |

## 系统架构

```
GitHub Actions (cron)
    │
    ├── 每小时: hourly.yml
    │   └── 抓取全部信源 → 质量过滤 → AI 过滤 → 去重 → AI 处理 → 更新首页 → 部署
    │
    └── 每天 08:00/20:00: daily.yml (UTC 00:00/12:00)
        └── 抓取 + 处理 → 生成日报 → 更新首页 → 部署

数据流:
  信源(RSS/API)
    → Fetcher（增量抓取 + 质量过滤 + AI 相关性过滤）
    → Processor（标题去重 + Claude API 智能处理 / 启发式降级）
    → Generator（Jinja2 模板渲染）
    → output/（静态 HTML）
    → GitHub Pages
```

## 信源列表

### 活跃信源（12 个正常工作）

| # | 信源 | 类别 | RSS URL | 特殊处理 |
|---|------|------|---------|----------|
| 1 | TechCrunch AI | 科技媒体 | `techcrunch.com/category/artificial-intelligence/feed/` | - |
| 2 | The Verge AI | 科技媒体 | `theverge.com/rss/ai-artificial-intelligence/index.xml` | - |
| 3 | Ars Technica | 科技媒体 | `feeds.arstechnica.com/arstechnica/index` | ai_filter |
| 4 | VentureBeat AI | 科技媒体 | `venturebeat.com/category/ai/feed/` | - |
| 5 | MIT Technology Review | 科技媒体 | `technologyreview.com/feed/` | ai_filter |
| 6 | Hacker News | 技术社区 | Algolia API `search_by_date` | 查询 AI 关键词, points>3 |
| 7 | Reddit AI | 技术社区 | `reddit.com/r/artificial+MachineLearning/hot/.rss` | - |
| 8 | OpenAI Blog | 官方博客 | `openai.com/news/rss.xml` | - |
| 9 | Google DeepMind Blog | 官方博客 | `deepmind.google/blog/rss.xml` | - |
| 10 | 量子位 | 国内媒体 | `qbitai.com/feed` | - |
| 11 | 36氪 | 国内媒体 | `36kr.com/feed` | ai_filter |
| 12 | Hugging Face Blog | 开源研究 | `huggingface.co/blog/feed.xml` | - |

### 已禁用信源（2 个，RSS 已失效）

| 信源 | 原因 |
|------|------|
| Anthropic News | RSS URL 返回 404，官方未提供公开 RSS |
| Meta AI Blog | RSS URL 返回 404 |

### 待观察信源

| 信源 | 状态 |
|------|------|
| 机器之心 | RSS 可用但可能需要特定网络环境 |
| Hacker News | 依赖帖子在时间窗口内积累足够热度 |

## 核心模块设计

### 1. Fetcher（src/fetcher.py）

**职责**：从各信源增量抓取文章，过滤无关和低质内容。

**关键机制**：
- **增量抓取**：通过 `data/state.json` 记录每个信源的 `last_fetch` 时间，只获取新文章
- **RSS 抓取**：`feedparser` 库解析，统一 User-Agent，30s 超时
- **Hacker News**：Algolia API，搜索 AI 相关关键词，`points>3` 过滤低热度
- **质量过滤**（`is_low_quality`）：过滤已删除帖子（`[Removed by Reddit]`、`[deleted]` 等）、空标题、过短标题（<5 字符）
- **AI 相关性过滤**：
  - 局部过滤：`ai_filter: true` 的综合类信源（Ars Technica、MIT Tech Review、36氪）先做关键词匹配
  - 全局过滤：所有文章最终都经过 AI 关键词检查，确保输出全部与 AI 相关
- **每源上限**：默认 30 篇/源（`max_articles`），防止历史全量 RSS 灌入
- **容错**：单个信源失败不影响其他信源

**AI 关键词**（24 个，配置在 `sources.yaml`）：
```
EN: AI, artificial intelligence, machine learning, deep learning, LLM, large language model,
    GPT, Claude, Gemini, neural network, transformer, AGI, chatbot, generative, diffusion,
    computer vision, NLP, robotics, autonomous
ZH: 人工智能, 大模型, 机器学习, 深度学习
```

**文章统一格式**：
```json
{
  "id": "md5(url)",
  "title": "...",
  "url": "...",
  "source": "TechCrunch AI",
  "source_zh": "TechCrunch AI",
  "category": "科技媒体",
  "language": "en",
  "published": "ISO 8601",
  "content_snippet": "前 500 字符"
}
```

### 2. Processor（src/processor.py）

**职责**：去重 + AI 智能处理（分类、摘要、评分）。

**标题去重**：
- 算法：`difflib.SequenceMatcher`
- 阈值：0.8（相似度 >= 80% 视为重复）
- 同时基于 article ID（URL 的 MD5）排除完全相同的文章

**Claude API 处理**（`use_ai=True` 时）：
- 模型：`claude-sonnet-4-6`
- 批量处理：每批 10 篇文章一次 API 调用，节省 token
- 结构化输出：`output_config.format` 使用 JSON Schema 约束响应格式
- System Prompt 指导 AI 输出：中文摘要（50-100 字）、分类、重要性评分、突发判断
- 错误处理：RateLimitError 和 APIStatusError 单独捕获，失败批次降级到启发式方案

**启发式降级方案**（`use_ai=False` 或 API 不可用时）：
- 重要性评分规则：
  - 基础分 2 分
  - 来自高权重信源（OpenAI Blog、Google DeepMind Blog、Anthropic News）+1
  - 标题/摘要包含高重要性关键词（launch、release、融资、收购、GPT-5 等）+1
  - 上限 5 分
- 分类推断：基于关键词匹配，统计各分类关键词命中数，取最高者
- 突发判断：importance >= 4 时标记为突发

**内容分类**（7 类）：
| 分类 | 关键词示例 |
|------|-----------|
| 模型发布 | release, launch, model, 发布, 模型, open source |
| 产品动态 | update, feature, app, product, api, 更新, 功能 |
| 融资收购 | funding, raise, acquire, 融资, 收购, 估值, IPO |
| 研究论文 | paper, research, arxiv, 论文, 研究, benchmark |
| 行业政策 | regulation, policy, law, 监管, 政策, 法规 |
| 开源项目 | github, open source, 开源, hugging face |
| 技术教程 | tutorial, guide, how to, 教程, 指南, 入门 |

**处理后文章增加字段**：
```json
{
  "summary_zh": "中文摘要 50-100 字",
  "category": "模型发布",
  "importance": 4,
  "is_breaking": true
}
```

### 3. Generator（src/generator.py）

**职责**：将处理后的文章渲染为静态 HTML 页面。

**模板引擎**：Jinja2，`autoescape=True`

**首页（index.html）**：
- 展示最近 3 天的文章
- 按重要性+时间倒序排列（重要性优先）
- 分类筛选按钮（纯 JS，动态显示当前存在的分类）
- 每篇文章显示：标题（可点击跳转原文）、中文摘要、来源徽章、分类标签、星级评分、相对时间
- 突发新闻高亮显示（橙色左边框 + 背景色 + 突发标签）
- 底部链接往期日报

**日报页（daily/YYYY-MM-DD.html）**：
- 当日全部文章
- 顶部"今日要点"概览：突发新闻列表 + 分类分布统计
- 返回首页链接

**时间显示**：
- < 1 小时：`N 分钟前`
- < 24 小时：`N 小时前`
- < 7 天：`N 天前`
- >= 7 天：`MM-DD HH:MM`

**样式（style.css）**：
- CSS 变量实现深色/浅色模式（`prefers-color-scheme`）
- 最大宽度 900px 居中
- 移动端适配（600px 断点隐藏序号）
- 突发新闻样式：橙色边框 + 浅橙背景

### 4. Storage（数据存储）

- `data/state.json`：信源抓取状态（last_fetch 时间 + 文章计数）
- `data/articles/YYYY-MM-DD.json`：每日文章数据，增量追加（基于 article ID 去重）
- `output/`：生成的静态网站目录
- `output/style.css`：从 templates 复制
- `output/daily/`：日报 HTML 文件

### 5. Main（src/main.py）

**两种运行模式**：
- `--mode hourly`：fetch_all → process_articles → save_processed → generate_index
- `--mode daily`：fetch_all → process_articles → save_processed → generate_daily（含首页更新）

## 目录结构

```
Agent实战-AI新闻速递/
├── requirement.md              # 需求文档
├── design.md                   # 设计文档（本文件）
├── README.md                   # 项目说明
├── requirements.txt            # Python 依赖
├── .gitignore                  # 忽略 data/ output/ .env 等
├── config/
│   └── sources.yaml            # 信源配置 + AI 关键词
├── src/
│   ├── __init__.py
│   ├── main.py                 # 主入口（hourly / daily 两种模式）
│   ├── fetcher.py              # 信源抓取（RSS + HN API + 过滤）
│   ├── processor.py            # 去重 + AI 处理 + 启发式降级
│   └── generator.py            # Jinja2 模板渲染
├── templates/
│   ├── index.html              # 首页模板（卡片流）
│   ├── daily.html              # 日报模板
│   └── style.css               # 样式（深色/浅色模式）
├── data/                       # 运行时数据（.gitignore）
│   ├── state.json              # 抓取状态
│   └── articles/
│       └── YYYY-MM-DD.json     # 每日文章
├── output/                     # 生成的静态网站（.gitignore）
│   ├── index.html
│   ├── style.css
│   └── daily/
│       └── YYYY-MM-DD.html
└── .github/
    └── workflows/
        ├── hourly.yml          # 每小时抓取 + 更新
        └── daily.yml           # 每日 08:00/20:00 汇总
```

## GitHub Actions 配置

### hourly.yml
- 触发：`cron: '0 * * * *'`（每小时）+ 手动触发
- 流程：checkout → setup-python 3.12 → install deps → `python main.py --mode hourly` → commit & push data/ output/
- Secrets：`ANTHROPIC_API_KEY`

### daily.yml
- 触发：`cron: '0 0 * * *'`（UTC 00:00 = 北京 08:00）+ `cron: '0 12 * * *'`（UTC 12:00 = 北京 20:00）+ 手动触发
- 流程：同 hourly，运行 `--mode daily`

## 部署清单

1. GitHub 仓库 Settings → Secrets → 添加 `ANTHROPIC_API_KEY`
2. GitHub Pages → Source 选择 `output/` 目录（或通过 Actions 部署）
3. 首次手动触发 hourly workflow 验证端到端流程

## 已知限制与后续优化

### 当前限制
- Anthropic、Meta AI 官方 RSS 已失效，暂无法覆盖
- 机器之心 RSS 在部分网络环境下不可用
- Hacker News 近期帖子热度不足时可能返回 0 条
- 无 Claude API Key 时降级为启发式方案，分类和评分精度有限

### 后续优化方向
- [ ] 补充更多信源（arXiv cs.AI、Twitter/X AI 大V 等）
- [ ] 数据自动清理（保留最近 30 天）
- [ ] GitHub Pages 自动部署优化
- [ ] 首页增加搜索功能
- [ ] 文章正文摘取（部分 RSS 只提供标题）
- [ ] 推送通知（突发新闻邮件/Webhook）
