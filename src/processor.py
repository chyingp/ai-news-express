import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import os
from openai import OpenAI

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_RETRIES = 3
SIMILARITY_THRESHOLD = 0.8
CLUSTER_THRESHOLD = 0.65

CATEGORIES = [
    "模型发布", "产品动态", "融资收购", "研究论文",
    "行业政策", "开源项目", "技术教程",
]

SYSTEM_PROMPT = """你是一个 AI 新闻分析助手。针对每篇文章，你需要输出：
1. title_zh: 中文标题。如果原标题是中文，原样返回；如果是英文，翻译为简洁准确的中文标题。
2. summary_zh: 中文摘要（50-100 字，简明扼要）
3. key_points: 核心要点数组（1-3 条，每条 15-25 字，提炼文章最关键的信息）
4. category: 分类标签，必须是以下之一：模型发布、产品动态、融资收购、研究论文、行业政策、开源项目、技术教程
5. importance: 重要性评分（1-5），评判标准：
   - 5: 行业重大突破、顶级公司核心产品发布
   - 4: 重要产品更新、大额融资、有影响力的开源发布
   - 3: 值得关注的行业动态、技术进展
   - 2: 一般性新闻、常规更新
   - 1: 边缘话题、影响有限
6. is_breaking: 是否为突发热点（同时满足：importance >= 4 且为最近 24 小时内发布的新闻）

请严格按以下 JSON 格式输出，不要输出任何其他内容：
{"articles": [{"title_zh": "", "summary_zh": "", "key_points": ["要点1", "要点2"], "category": "", "importance": 3, "is_breaking": false}, ...]}
数组中每个元素对应输入中的一篇文章，顺序一致。"""


def deduplicate(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    unique = []
    seen_ids = set()

    for article in articles:
        if article["id"] in seen_ids:
            continue

        is_dup = False
        for kept in unique:
            ratio = SequenceMatcher(None, article["title"], kept["title"]).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                is_dup = True
                logger.debug(f"Duplicate: '{article['title']}' ~ '{kept['title']}' ({ratio:.2f})")
                break

        if not is_dup:
            unique.append(article)
            seen_ids.add(article["id"])

    removed = len(articles) - len(unique)
    if removed:
        logger.info(f"Dedup: {len(articles)} -> {len(unique)} ({removed} removed)")
    return unique


def cluster_articles(articles: list[dict]) -> list[dict]:
    if not articles:
        return []

    cluster_id = 0
    assigned = [False] * len(articles)

    for i, a in enumerate(articles):
        a["cluster_id"] = -1
        a["is_cluster_main"] = True
        a["cluster_children"] = []

    for i in range(len(articles)):
        if assigned[i]:
            continue

        group = [i]
        assigned[i] = True

        for j in range(i + 1, len(articles)):
            if assigned[j]:
                continue
            if articles[i].get("category") != articles[j].get("category"):
                continue
            ratio = SequenceMatcher(None, articles[i]["title"], articles[j]["title"]).ratio()
            if ratio >= CLUSTER_THRESHOLD:
                group.append(j)
                assigned[j] = True

        if len(group) == 1:
            articles[i]["cluster_id"] = cluster_id
            cluster_id += 1
            continue

        main_idx = max(group, key=lambda idx: articles[idx].get("importance", 0))
        for idx in group:
            articles[idx]["cluster_id"] = cluster_id
            if idx == main_idx:
                articles[idx]["is_cluster_main"] = True
                articles[idx]["cluster_children"] = [
                    {"title": articles[j]["title"], "title_zh": articles[j].get("title_zh", ""),
                     "url": articles[j]["url"], "source_zh": articles[j].get("source_zh", "")}
                    for j in group if j != main_idx
                ]
            else:
                articles[idx]["is_cluster_main"] = False

        cluster_id += 1

    main_count = sum(1 for a in articles if a["is_cluster_main"])
    clustered = sum(1 for a in articles if not a["is_cluster_main"])
    if clustered:
        logger.info(f"Clustering: {len(articles)} articles -> {main_count} groups ({clustered} folded)")

    return articles


def _build_articles_prompt(articles: list[dict]) -> str:
    parts = []
    for i, a in enumerate(articles):
        parts.append(
            f"[Article {i+1}]\n"
            f"Title: {a['title']}\n"
            f"Source: {a['source']}\n"
            f"URL: {a['url']}\n"
            f"Published: {a.get('published', '')}\n"
            f"Snippet: {a.get('content_snippet', '')[:300]}"
        )
    return "\n\n".join(parts)


def _process_batch(client: OpenAI, batch: list[dict]) -> list[dict]:
    prompt = _build_articles_prompt(batch)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model="deepseek-v4-pro",
                max_tokens=8192,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请分析以下 {len(batch)} 篇文章：\n\n{prompt}"},
                ],
                extra_body={"enable_thinking": False},
            )
            text = response.choices[0].message.content
            results = json.loads(text).get("articles", [])
            if isinstance(results, list):
                return results
            logger.warning(f"Unexpected response shape (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            logger.warning(f"API/parse error (attempt {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)

    logger.error(f"Batch failed after {MAX_RETRIES} attempts")
    return []


def process_batch_with_fallback(client: OpenAI, batch: list[dict]) -> int:
    """Update each article in ``batch`` in place with AI results.

    On a count mismatch the batch is retried one article at a time, so a single
    problematic article can no longer drop the translations for the other nine.
    Articles that still fail fall back to heuristic scoring (no translation).
    Returns the number of articles successfully processed by the AI.
    """
    results = _process_batch(client, batch)
    if len(results) == len(batch):
        for article, ai_result in zip(batch, results):
            article.update(ai_result)
        return len(batch)

    if len(batch) > 1:
        logger.warning(
            f"Expected {len(batch)} results, got {len(results)}; retrying per-article"
        )
        return sum(process_batch_with_fallback(client, [a]) for a in batch)

    logger.warning(f"AI failed for '{batch[0]['title'][:60]}', using heuristic")
    batch[0].update(_heuristic_score(batch[0]))
    return 0


HIGH_IMPORTANCE_SOURCES = {"OpenAI Blog", "Google DeepMind Blog", "Anthropic News"}

HIGH_IMPORTANCE_KEYWORDS = [
    "launch", "release", "announce", "introduce", "unveil",
    "发布", "推出", "上线", "开源", "融资", "收购", "突破",
    "GPT-5", "GPT-6", "Claude", "Gemini", "Llama",
    "billion", "亿", "AGI", "breakthrough",
]

CATEGORY_KEYWORDS = {
    "模型发布": ["new model", "model release", "foundation model", "语言模型", "LLM",
                "发布模型", "模型发布", "模型", "weights", "parameter", "fine-tune", "checkpoint"],
    "融资收购": ["funding", "raise", "acquire", "merger", "valuation", "investment", "investor",
                "融资", "收购", "估值", "IPO", "partnership", "partner", "合作"],
    "研究论文": ["paper", "research", "arxiv", "study", "论文", "研究", "benchmark", "evaluation"],
    "行业政策": ["regulation", "policy", "law", "ban", "govern", "legislation", "compliance",
                "监管", "政策", "法规", "合规"],
    "开源项目": ["github", "open source", "repository", "开源", "repo", "hugging face", "apache license", "MIT license"],
    "技术教程": ["tutorial", "guide", "how to", "教程", "指南", "入门", "实战",
                "course", "learn", "workshop", "academy", "课程"],
    "产品动态": ["update", "feature", "app", "product", "api", "platform", "service", "tool",
                "launch", "release", "introduce", "announce", "更新", "功能", "产品", "升级", "发布", "推出", "上线"],
}


def _is_within_24h(published: str) -> bool:
    try:
        dt = datetime.fromisoformat(published)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() < 86400
    except (ValueError, TypeError):
        return False


def _heuristic_score(article: dict) -> dict:
    text = f"{article['title']} {article.get('content_snippet', '')}".lower()
    source = article.get("source", "")

    score = 2
    if source in HIGH_IMPORTANCE_SOURCES:
        score += 1
    if any(kw.lower() in text for kw in HIGH_IMPORTANCE_KEYWORDS):
        score += 1

    category = "产品动态"
    best_match = 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw.lower() in text)
        if matches > best_match:
            best_match = matches
            category = cat

    score = min(score, 5)
    recent = _is_within_24h(article.get("published", ""))

    raw_snippet = article.get("content_snippet", "")
    clean_text = re.sub(r'<[^>]+>', '', raw_snippet).strip()
    clean_text = re.sub(r'\s+', ' ', clean_text)

    key_points = []
    if clean_text:
        sentences = re.split(r'[.。!！?？;；\n]+', clean_text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        key_points = sentences[:3]

    return {
        "title_zh": "",
        "summary_zh": clean_text[:150] if clean_text else "",
        "key_points": key_points,
        "category": category,
        "importance": score,
        "is_breaking": score >= 4 and recent,
    }


def process_articles(articles: list[dict], use_ai: bool = True) -> list[dict]:
    articles = deduplicate(articles)
    if not articles:
        return []

    if use_ai:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            logger.warning("DASHSCOPE_API_KEY not set, falling back to heuristic scoring")
            use_ai = False
        else:
            client = OpenAI(
                api_key=api_key,
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            )

    processed = []

    if not use_ai:
        for a in articles:
            a.update(_heuristic_score(a))
            processed.append(a)
        logger.info(f"Heuristic scoring: {len(processed)} articles")
    else:
        ai_count = 0
        for i in range(0, len(articles), BATCH_SIZE):
            batch = articles[i:i + BATCH_SIZE]
            logger.info(f"Processing batch {i // BATCH_SIZE + 1} ({len(batch)} articles)")
            ai_count += process_batch_with_fallback(client, batch)
            processed.extend(batch)
        logger.info(
            f"AI processed {ai_count}/{len(articles)} articles "
            f"({len(articles) - ai_count} heuristic)"
        )

    for a in processed:
        if a.get("is_breaking") and not _is_within_24h(a.get("published", "")):
            a["is_breaking"] = False

    processed = cluster_articles(processed)

    breaking = [a for a in processed if a.get("is_breaking")]
    if breaking:
        logger.info(f"Breaking news: {len(breaking)} articles")
        for a in breaking:
            logger.info(f"  [{a['importance']}★] {a['title']}")

    return processed


def save_processed(articles: list[dict], date_str: str):
    data_dir = Path(__file__).parent.parent / "data" / "articles"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{date_str}.json"

    existing = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)

    seen_ids = {a["id"] for a in existing}
    new_articles = [a for a in articles if a["id"] not in seen_ids]
    merged = existing + new_articles

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(new_articles)} new articles to {path} (total: {len(merged)})")
    return merged
