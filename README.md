# AI 新闻速递

自动化 AI 资讯聚合系统 —— 从 14 个国内外高质量信源抓取 AI 相关资讯，经 DeepSeek v4 Pro 智能处理后生成静态网页，部署到 GitHub Pages。

## 功能

- **每小时自动抓取**：覆盖 TechCrunch、The Verge、OpenAI Blog、Google DeepMind、Reddit、量子位、36氪等 14 个信源
- **智能内容处理**：DeepSeek v4 Pro 批量生成中文摘要、分类标签、重要性评分（1-5 星）
- **突发新闻标记**：重要性 >= 4 星自动标记为突发热点，首页高亮展示
- **每日汇总日报**：08:00 / 20:00 自动生成包含"今日要点"的日报页面
- **分类筛选**：模型发布、产品动态、融资收购、研究论文、行业政策、开源项目、技术教程
- **多层过滤**：AI 关键词过滤 + 低质内容清理 + 标题去重（相似度 0.8 阈值）
- **深色模式**：跟随系统偏好自动切换

## 快速开始

### 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 配置环境变量

复制 `.env.example` 创建 `.env` 文件，填入你的 API Key：

```bash
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY
```

> 不设置 API Key 也可运行，会自动降级为启发式评分模式。

### 本地运行

```bash
# 每小时模式：抓取 + 处理 + 生成首页
cd src && python main.py --mode hourly

# 日报模式：抓取 + 处理 + 生成日报 + 更新首页
cd src && python main.py --mode daily
```

生成的页面在 `output/` 目录，浏览器打开 `output/index.html` 即可查看。

### GitHub Actions 部署

1. Fork 本仓库
2. Settings → Secrets and variables → Actions → 添加 `DASHSCOPE_API_KEY`
3. Settings → Pages → Source 选择部署方式
4. Actions 页面手动触发 `Hourly News Check` 验证

## 项目结构

```
├── config/sources.yaml     # 信源配置 + AI 关键词列表
├── src/
│   ├── main.py             # 主入口（hourly / daily 两种模式）
│   ├── fetcher.py          # 信源抓取（RSS + Hacker News API）
│   ├── processor.py        # 去重 + DeepSeek API 处理 + 启发式降级
│   └── generator.py        # Jinja2 模板渲染静态页面
├── templates/              # HTML 模板 + CSS 样式
├── .github/workflows/      # GitHub Actions 定时任务
├── design.md               # 详细设计文档
└── requirements.txt        # Python 依赖
```

## 信源覆盖

| 类别 | 信源 |
|------|------|
| 国际科技媒体 | TechCrunch AI, The Verge AI, Ars Technica, VentureBeat AI, MIT Technology Review |
| 技术社区 | Hacker News (Algolia API), Reddit r/artificial + r/MachineLearning |
| AI 公司博客 | OpenAI Blog, Google DeepMind Blog |
| 国内媒体 | 量子位, 36氪 |
| 开源研究 | Hugging Face Blog |

## 技术栈

- **Python 3.12**：feedparser + openai SDK + jinja2 + pyyaml + requests
- **DeepSeek v4 Pro**：通过阿里云 DashScope 兼容接口调用，结构化 JSON 输出
- **GitHub Actions**：cron 定时触发
- **GitHub Pages**：静态网站托管
