import json
import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from processor import cluster_for_display

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
OUTPUT_DIR = PROJECT_ROOT / "output"
DATA_DIR = PROJECT_ROOT / "data" / "articles"

CATEGORIES = [
    "模型发布", "产品动态", "融资收购", "研究论文",
    "行业政策", "开源项目", "技术教程",
]

CST = timezone(timedelta(hours=8))

# 热度分 = 重要性 × 时间衰减。每过 HALF_LIFE_HOURS 小时，热度减半，
# 让新发布的资讯逐步盖过旧的高分资讯，而不是旧新闻永久霸榜。
HALF_LIFE_HOURS = 18.0

# 首页"专家观点"板块最多展示的推文条数
EXPERT_LIMIT = 12


def _is_expert(a: dict) -> bool:
    """是否来自 X/Twitter 专家信源（经 Nitter 抓取）。"""
    return a.get("source_type") == "nitter" or a.get("source", "").endswith("(X)")


def _hot_score(article: dict) -> float:
    importance = article.get("importance", 0) or 0
    try:
        dt = datetime.fromisoformat(article.get("published", ""))
        age_hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    except (ValueError, TypeError):
        age_hours = 72.0  # 发布时间缺失时按较旧处理
    return importance * (0.5 ** (age_hours / HALF_LIFE_HOURS))


def _init_output():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    css_src = TEMPLATES_DIR / "style.css"
    css_dst = OUTPUT_DIR / "style.css"
    if css_src.exists():
        shutil.copy2(css_src, css_dst)
    daily_dir = OUTPUT_DIR / "daily"
    daily_dir.mkdir(exist_ok=True)


def _get_jinja_env():
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )


def _format_published(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(CST)
        now = datetime.now(CST)
        diff = now - dt
        if diff.total_seconds() < 3600:
            return f"{int(diff.total_seconds() / 60)} 分钟前"
        if diff.total_seconds() < 86400:
            return f"{int(diff.total_seconds() / 3600)} 小时前"
        if diff.days < 7:
            return f"{diff.days} 天前"
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def _format_published_full(iso_str: str) -> str:
    """绝对发布时间（CST，精确到分），用于 tooltip / 语义化 <time> 标签。"""
    try:
        return datetime.fromisoformat(iso_str).astimezone(CST).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ""


def _is_within_24h(iso_str: str) -> bool:
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() < 86400
    except (ValueError, TypeError):
        return False


def _prepare_articles(articles: list[dict]) -> list[dict]:
    for a in articles:
        a["published_display"] = _format_published(a.get("published", ""))
        a["published_full"] = _format_published_full(a.get("published", ""))
        a.setdefault("importance", 2)
        a.setdefault("is_breaking", False)
        a.setdefault("category", "产品动态")
        a.setdefault("summary_zh", "")
        a.setdefault("title_zh", "")
        a.setdefault("key_points", [])
        a.setdefault("is_cluster_main", True)
        a.setdefault("cluster_children", [])

        if a["is_breaking"] and not _is_within_24h(a.get("published", "")):
            a["is_breaking"] = False

        a["hot_score"] = _hot_score(a)

    articles.sort(key=lambda a: (a.get("hot_score", 0), a.get("published", "")), reverse=True)
    return articles


def _load_recent_articles(days: int = 3) -> list[dict]:
    all_articles = []
    today = datetime.now(CST).date()
    for i in range(days):
        date = today - timedelta(days=i)
        path = DATA_DIR / f"{date.isoformat()}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                all_articles.extend(json.load(f))
    return all_articles


def _get_daily_links(limit: int = 7) -> list[dict]:
    daily_dir = OUTPUT_DIR / "daily"
    if not daily_dir.exists():
        return []
    files = sorted(daily_dir.glob("*.html"), reverse=True)[:limit]
    links = []
    for f in files:
        date_str = f.stem
        links.append({"url": f"daily/{f.name}", "label": date_str})
    return links


def generate_index(articles: list[dict] | None = None):
    _init_output()
    env = _get_jinja_env()

    if articles is None:
        articles = _load_recent_articles(days=3)

    articles = _prepare_articles(articles)

    # 专家推文（X/Twitter）单独成块，按时间倒序，不与新闻混排
    expert_articles = [a for a in articles if _is_expert(a)]
    expert_articles.sort(key=lambda a: a.get("published", ""), reverse=True)
    experts = expert_articles[:EXPERT_LIMIT]

    # 在完整新闻集上去重归并（近 3 天累积），同一事件只保留质量最高的一条，
    # 其余折叠为"相关报道"。必须在此处去重，入库时只看得到单次新抓的文章。
    news = [a for a in articles if not _is_expert(a)]
    news = cluster_for_display(news)

    main_articles = [a for a in news if a.get("is_cluster_main", True)]

    headlines = [a for a in main_articles if a.get("importance", 0) >= 3][:10]

    present_cats = sorted(set(a["category"] for a in main_articles if a.get("category")))
    cat_counts = {}
    for a in main_articles:
        cat = a.get("category", "")
        if cat:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    breaking_count = sum(1 for a in main_articles if a.get("is_breaking"))

    template = env.get_template("index.html")
    html = template.render(
        articles=main_articles,
        headlines=headlines,
        experts=experts,
        categories=present_cats,
        cat_counts=cat_counts,
        total_count=len(main_articles),
        breaking_count=breaking_count,
        updated_at=datetime.now(CST).strftime("%Y-%m-%d %H:%M CST"),
        source_count=len(set(a.get("source", "") for a in articles)),
        daily_links=_get_daily_links(),
    )

    out_path = OUTPUT_DIR / "index.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"Generated index.html with {len(main_articles)} articles ({len(news) - len(main_articles)} clustered)")
    return out_path


def generate_daily_overview(articles: list[dict]) -> str:
    if not articles:
        return ""

    breaking = [a for a in articles if a.get("is_breaking")]
    summary_parts = []
    if breaking:
        summary_parts.append(f"今日有 {len(breaking)} 条重要资讯：")
        for a in breaking[:5]:
            label = a.get("summary_zh") or a.get("title_zh") or a["title"]
            summary_parts.append(f"• {label}")

    cat_counts = {}
    for a in articles:
        cat = a.get("category", "其他")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    top_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    summary_parts.append(f"资讯分布：{'、'.join(f'{c} {n} 条' for c, n in top_cats)}。")

    return "\n".join(summary_parts)


def generate_daily(date_str: str | None = None):
    _init_output()
    env = _get_jinja_env()

    if date_str is None:
        date_str = datetime.now(CST).strftime("%Y-%m-%d")

    path = DATA_DIR / f"{date_str}.json"
    if not path.exists():
        logger.warning(f"No data for {date_str}")
        articles = []
    else:
        with open(path, "r", encoding="utf-8") as f:
            articles = json.load(f)

    articles = _prepare_articles(articles)
    articles = cluster_for_display(articles)
    main_articles = [a for a in articles if a.get("is_cluster_main", True)]
    overview = generate_daily_overview(main_articles)
    present_cats = sorted(set(a["category"] for a in main_articles if a.get("category")))

    template = env.get_template("daily.html")
    html = template.render(
        date=date_str,
        articles=main_articles,
        categories=present_cats,
        overview=overview,
        breaking_count=len([a for a in main_articles if a.get("is_breaking")]),
        source_count=len(set(a.get("source", "") for a in articles)),
    )

    daily_dir = OUTPUT_DIR / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    out_path = daily_dir / f"{date_str}.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"Generated daily report: {out_path}")
    generate_index()
    return out_path
