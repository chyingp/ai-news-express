import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
import yaml

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
STATE_FILE = DATA_DIR / "state.json"


def load_sources(config_path: Optional[Path] = None) -> dict:
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config" / "sources.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def fetch_rss(source: dict, last_fetch: Optional[str] = None) -> list[dict]:
    url = source["url"]
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "AI-News-Bot/1.0 (RSS Reader)"
        })
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.warning(f"[{source['name']}] RSS fetch failed: {e}")
        return []

    last_dt = None
    if last_fetch:
        last_dt = datetime.fromisoformat(last_fetch)

    articles = []
    for entry in feed.entries:
        published = None
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                from time import mktime
                published = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                break

        if published is None:
            published = datetime.now(timezone.utc)

        if last_dt and published <= last_dt:
            continue

        link = getattr(entry, "link", "")
        if not link:
            continue

        content_snippet = ""
        if hasattr(entry, "summary"):
            content_snippet = entry.summary[:500]
        elif hasattr(entry, "description"):
            content_snippet = entry.description[:500]

        articles.append({
            "id": article_id(link),
            "title": getattr(entry, "title", ""),
            "url": link,
            "source": source["name"],
            "source_zh": source.get("name_zh", source["name"]),
            "category": source.get("category", ""),
            "language": source.get("language", "en"),
            "published": published.isoformat(),
            "content_snippet": content_snippet,
        })

    logger.info(f"[{source['name']}] Fetched {len(articles)} new articles")
    return articles


DEFAULT_NITTER = "https://nitter.net"


def _is_retweet(title: str) -> bool:
    t = title.strip()
    return t.startswith("RT @") or t.startswith("RT by ") or t.startswith("R to @")


def fetch_nitter(source: dict, config: dict, last_fetch: Optional[str] = None) -> list[dict]:
    """通过 Nitter 把 X/Twitter 用户时间线桥接为 RSS 抓取。

    X 已无官方 RSS、API 转为付费，故经 Nitter 实例代理。公共实例可能失效，
    届时只需修改 config 顶层的 nitter_instance。失败时 fetch_rss 会返回空列表，
    不影响其他信源。
    """
    instance = (config.get("nitter_instance") or DEFAULT_NITTER).rstrip("/")
    handle = source["handle"].lstrip("@")
    rss_source = {**source, "url": f"{instance}/{handle}/rss"}
    articles = fetch_rss(rss_source, last_fetch)

    # 过滤转推与纯回复，保留专家本人的原创观点
    kept = [a for a in articles if not _is_retweet(a["title"])]
    if len(kept) != len(articles):
        logger.info(f"[{source['name']}] Filtered retweets/replies: {len(articles)} -> {len(kept)}")
    return kept


def fetch_hackernews(source: dict, last_fetch: Optional[str] = None) -> list[dict]:
    ai_tags = ["artificial-intelligence", "machine-learning", "deep-learning", "llm"]
    query_terms = "AI OR LLM OR GPT OR Claude OR Gemini OR \"machine learning\" OR \"deep learning\" OR \"artificial intelligence\""

    now_ts = int(datetime.now(timezone.utc).timestamp())
    one_day_ago = now_ts - 86400

    params = {
        "query": query_terms,
        "tags": "story",
        "hitsPerPage": 30,
        "numericFilters": f"created_at_i>{one_day_ago},points>3",
    }

    if last_fetch:
        last_dt = datetime.fromisoformat(last_fetch)
        params["numericFilters"] = f"created_at_i>{int(last_dt.timestamp())},points>3"

    try:
        resp = requests.get(source["url"], params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[Hacker News] API fetch failed: {e}")
        return []

    articles = []
    for hit in data.get("hits", []):
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
        created_at = hit.get("created_at", "")
        published = datetime.now(timezone.utc)
        if created_at:
            try:
                published = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                pass

        points = hit.get("points", 0) or 0

        articles.append({
            "id": article_id(url),
            "title": hit.get("title", ""),
            "url": url,
            "source": "Hacker News",
            "source_zh": "Hacker News",
            "category": "技术社区",
            "language": "en",
            "published": published.isoformat(),
            "content_snippet": f"Points: {points}, Comments: {hit.get('num_comments', 0)}",
        })

    logger.info(f"[Hacker News] Fetched {len(articles)} new articles")
    return articles


JUNK_PATTERNS = [
    "[removed by reddit]",
    "[deleted by user]",
    "[deleted]",
    "[removed]",
]


def is_ai_related(article: dict, ai_keywords: list[str]) -> bool:
    text = f"{article['title']} {article['content_snippet']}".lower()
    return any(kw.lower() in text for kw in ai_keywords)


def is_low_quality(article: dict) -> bool:
    title = article.get("title", "").strip()
    if not title or len(title) < 5:
        return True
    title_lower = title.lower()
    if any(p in title_lower for p in JUNK_PATTERNS):
        return True
    return False


def fetch_all(config: Optional[dict] = None) -> list[dict]:
    if config is None:
        config = load_sources()

    state = load_state()
    ai_keywords = config.get("ai_keywords", [])
    all_articles = []

    for source in config["sources"]:
        if not source.get("enabled", True):
            continue

        name = source["name"]
        last_fetch = state.get(name, {}).get("last_fetch")

        if source["type"] == "hackernews":
            articles = fetch_hackernews(source, last_fetch)
        elif source["type"] == "nitter":
            articles = fetch_nitter(source, config, last_fetch)
        else:
            articles = fetch_rss(source, last_fetch)

        before_quality = len(articles)
        articles = [a for a in articles if not is_low_quality(a)]
        if before_quality != len(articles):
            logger.info(f"[{name}] Quality filter: {before_quality} -> {len(articles)}")

        if source.get("ai_filter"):
            before = len(articles)
            articles = [a for a in articles if is_ai_related(a, ai_keywords)]
            logger.info(f"[{name}] AI filter: {before} -> {len(articles)}")

        max_per_source = source.get("max_articles", 30)
        if len(articles) > max_per_source:
            articles = articles[:max_per_source]
            logger.info(f"[{name}] Capped to {max_per_source} articles")

        all_articles.extend(articles)

        state[name] = {
            "last_fetch": datetime.now(timezone.utc).isoformat(),
            "article_count": len(articles),
        }

    save_state(state)

    before_global = len(all_articles)
    all_articles = [a for a in all_articles if is_ai_related(a, ai_keywords)]
    if before_global != len(all_articles):
        logger.info(f"Global AI filter: {before_global} -> {len(all_articles)}")

    all_articles.sort(key=lambda a: a["published"], reverse=True)
    logger.info(f"Total: {len(all_articles)} new articles from {len(config['sources'])} sources")
    return all_articles
