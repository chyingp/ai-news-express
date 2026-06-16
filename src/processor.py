import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import os
from openai import OpenAI

import companies as company_registry

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
MAX_RETRIES = 3
SIMILARITY_THRESHOLD = 0.8
CLUSTER_THRESHOLD = 0.65

CATEGORIES = [
    "模型发布", "产品动态", "融资收购", "研究论文",
    "行业政策", "开源项目", "技术教程",
]

ARTICLES_DIR = Path(__file__).parent.parent / "data" / "articles"

# AI 处理产出的字段；复用已处理文章时只回填这些，其余原始字段保持不变。
_AI_FIELDS = ("title_zh", "summary_zh", "key_points", "category", "importance", "is_breaking", "companies")

# 复用必须齐备的关键字段（缺任一则不能复用，需重新处理）。
_REQUIRED_FIELDS = ("title_zh", "summary_zh", "category", "importance")

# 处理逻辑版本号。当 prompt、输出结构、分类体系等发生显著变化、
# 使旧的处理结果不再可信时，递增此值，存档中旧版本结果将自动失效重做。
PROC_VERSION = 1

SYSTEM_PROMPT = f"""你是一个 AI 新闻分析助手。针对每篇文章，你需要输出：
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
7. companies: 这篇新闻"主要讲述"的巨头公司 key 数组（可为空 []）。只有当文章主要围绕某公司
   （其产品/动作/发布/财务/表态/人事等）时才列入；若该公司仅作为竞争对手、对比、背景被
   顺带提及（如"追赶 OpenAI""挑战谷歌"），不要列入。可选 key：{company_registry.prompt_catalog()}

请严格按以下 JSON 格式输出，不要输出任何其他内容：
{{"articles": [{{"title_zh": "", "summary_zh": "", "key_points": ["要点1", "要点2"], "category": "", "importance": 3, "is_breaking": false, "companies": []}}, ...]}}
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


def _assign_clusters(articles: list[dict], groups: list[list[int]]) -> list[dict]:
    """根据分组结果给文章打聚类标记。

    ``groups`` 是下标分组列表，须覆盖每篇文章恰好一次（含单条分组）。
    每组保留"质量最高"的一条为 main：先比 importance，再比摘要长度，
    最后比发布时间（取更新的），其余折叠为 cluster_children。
    """
    for cid, group in enumerate(groups):
        if len(group) == 1:
            a = articles[group[0]]
            a["cluster_id"] = cid
            a["is_cluster_main"] = True
            a["cluster_children"] = []
            continue

        main_idx = max(group, key=lambda idx: (
            articles[idx].get("importance", 0),
            len(articles[idx].get("summary_zh", "") or ""),
            articles[idx].get("published", ""),
        ))
        for idx in group:
            a = articles[idx]
            a["cluster_id"] = cid
            if idx == main_idx:
                a["is_cluster_main"] = True
                a["cluster_children"] = [
                    {"title": articles[j]["title"], "title_zh": articles[j].get("title_zh", ""),
                     "url": articles[j]["url"], "source_zh": articles[j].get("source_zh", "")}
                    for j in group if j != main_idx
                ]
            else:
                a["is_cluster_main"] = False

    folded = sum(1 for a in articles if not a["is_cluster_main"])
    if folded:
        main_count = sum(1 for a in articles if a["is_cluster_main"])
        logger.info(f"Clustering: {len(articles)} articles -> {main_count} groups ({folded} folded)")
    return articles


def cluster_articles(articles: list[dict]) -> list[dict]:
    """词面聚类（兜底）：同分类下标题字符相似度超阈值则归并。

    仅在无法调用模型时使用；正常情况下由 llm_cluster_articles 做语义去重。
    """
    if not articles:
        return []

    assigned = [False] * len(articles)
    groups: list[list[int]] = []

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
        groups.append(group)

    return _assign_clusters(articles, groups)


CLUSTER_SYSTEM_PROMPT = """你是 AI 资讯去重助手。输入每行是一条资讯，格式为「编号<TAB>标题（来源）」。
请找出报道**同一个新闻事件**的条目并归为一组。判断标准：
- 算重复：同一事件/同一动作的不同报道，例如同一笔融资或同一次发债、同一款产品或模型的同一次发布、同一则人事变动、对同一消息的多家报道（哪怕措辞、语言、角度不同）。
- 不算重复：不同的课程/产品/模型、同一公司或同一个人的不同事件或不同言论、仅主题相关但事件不同的内容。
- 尤其注意：不同公司、不同标的、不同金额的交易，即便同属"融资/收购/IPO"等大主题，也**不是**同一事件，不要归并（如某公司发债 vs 另一公司被收购）。
- "晚报/日报/快讯/汇总/盘点"这类一条覆盖多个事件的合集，**不要**与任何具体单一事件归并，让它单独成条。
只返回包含 2 条及以上的重复分组，单独成条的不要返回。
严格按如下 JSON 返回，不要输出多余内容：{"groups": [[编号, 编号, ...], ...]}"""


def _build_cluster_prompt(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles):
        title = a.get("title_zh") or a.get("title", "")
        lines.append(f"{i}\t{title}\t（{a.get('source_zh', '')}）")
    return "\n".join(lines)


def _normalize_groups(raw, n: int) -> list[list[int]]:
    """把模型返回的分组清洗为合法的下标划分。

    剔除越界/重复/非法下标，每个下标至多归入一组；未被分组的下标各自成单条组，
    保证返回结果覆盖 0..n-1 恰好一次。
    """
    seen: set[int] = set()
    groups: list[list[int]] = []
    if isinstance(raw, list):
        for g in raw:
            if not isinstance(g, list):
                continue
            members = []
            for x in g:
                if isinstance(x, bool):
                    continue
                try:
                    idx = int(x)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < n and idx not in seen and idx not in members:
                    members.append(idx)
            if len(members) >= 2:
                seen.update(members)
                groups.append(members)
    for i in range(n):
        if i not in seen:
            groups.append([i])
    return groups


def llm_cluster_articles(client: OpenAI, articles: list[dict]) -> list[dict]:
    """用模型按"是否同一事件"做语义去重归并。

    比词面相似度更可靠：能识别跨语言、跨分类、措辞不同的同一事件（如英伟达发债的
    多家报道），同时不会把"同主题不同事件"（如不同课程、同一人的不同言论）误并。
    失败时回退到词面聚类 cluster_articles。
    """
    if len(articles) < 2:
        return cluster_articles(articles)

    prompt = _build_cluster_prompt(articles)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model="deepseek-v4-pro",
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": CLUSTER_SYSTEM_PROMPT},
                    {"role": "user", "content": f"共 {len(articles)} 条资讯：\n\n{prompt}"},
                ],
                extra_body={"enable_thinking": False},
            )
            raw = json.loads(response.choices[0].message.content).get("groups", [])
            groups = _normalize_groups(raw, len(articles))
            return _assign_clusters(articles, groups)
        except Exception as e:
            logger.warning(f"LLM clustering error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    logger.warning("LLM clustering failed, falling back to lexical clustering")
    return cluster_articles(articles)


def cluster_for_display(articles: list[dict]) -> list[dict]:
    """在"实际要展示的完整文章集"上做去重归并，供页面生成时调用。

    去重必须在这里做、而非入库时：hourly 模式每次只处理新抓到的少量文章，
    跨运行/跨天累积的同一事件（如不同小时分别抓到的"英伟达发债"）只有在完整
    展示集上比较才能发现。有 API key 时用语义去重，否则回退词面聚类。
    """
    if len(articles) < 2:
        return _assign_clusters(articles, [[i] for i in range(len(articles))])

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        return cluster_articles(articles)

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    return llm_cluster_articles(client, articles)


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


def _is_reusable(prev: dict | None) -> bool:
    """已存档的处理结果是否可以直接复用，跳过重新调用 AI。

    不可复用的两类情况，会回退到重新处理：
    - 缺关键字段（title_zh / summary_zh / category / importance 不全）；
    - 处理逻辑版本不一致（PROC_VERSION 改变，旧结果视为过期）。
      早于版本机制的历史数据默认按版本 1 处理。
    """
    if not prev:
        return False
    if any(prev.get(k) in (None, "", []) for k in _REQUIRED_FIELDS):
        return False
    return prev.get("proc_version", 1) == PROC_VERSION


def process_articles(
    articles: list[dict],
    use_ai: bool = True,
    known: dict[str, dict] | None = None,
) -> list[dict]:
    articles = deduplicate(articles)
    if not articles:
        return []

    known = known or {}

    # 复用此前已处理过的文章结果，不再重复调用模型。这样即便修改网站逻辑、
    # 重新生成页面或重跑流程，已翻译/摘要过的文章也不会被反复处理，节省 API 调用。
    reused, todo = [], []
    for a in articles:
        prev = known.get(a["id"])
        if _is_reusable(prev):
            a.update({k: prev[k] for k in _AI_FIELDS if k in prev})
            a["processed"] = True
            a["proc_version"] = PROC_VERSION
            reused.append(a)
        else:
            todo.append(a)
    if reused:
        logger.info(f"Reusing {len(reused)} already-processed articles (skipped AI)")

    client = None
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

    if todo and not use_ai:
        for a in todo:
            a.update(_heuristic_score(a))
            processed.append(a)
        logger.info(f"Heuristic scoring: {len(processed)} articles")
    elif todo:
        ai_count = 0
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            logger.info(f"Processing batch {i // BATCH_SIZE + 1} ({len(batch)} articles)")
            ai_count += process_batch_with_fallback(client, batch)
            processed.extend(batch)
        logger.info(
            f"AI processed {ai_count}/{len(todo)} articles "
            f"({len(todo) - ai_count} heuristic)"
        )

    for a in processed:
        a["processed"] = True
        a["proc_version"] = PROC_VERSION

    processed = reused + processed

    for a in processed:
        if a.get("is_breaking") and not _is_within_24h(a.get("published", "")):
            a["is_breaking"] = False

    # 去重归并放到页面生成时进行（见 cluster_for_display）：hourly 模式下这里只
    # 拿到本次新抓的少量文章，无法发现跨运行/跨天累积的同一事件重复。

    breaking = [a for a in processed if a.get("is_breaking")]
    if breaking:
        logger.info(f"Breaking news: {len(breaking)} articles")
        for a in breaking:
            logger.info(f"  [{a['importance']}★] {a['title']}")

    return processed


COMPANY_SYSTEM_PROMPT = f"""你是 AI 行业分析助手。给定若干条新闻（中文标题+摘要），判断每条"主要讲述"的巨头公司。
规则：只有当文章主要围绕某公司（其产品/动作/发布/财务/表态/人事等）时才归类；若该公司仅作为
竞争对手、对比、背景被顺带提及（如"追赶 OpenAI""挑战谷歌"），不要归类。可为空。
可选公司 key：{company_registry.prompt_catalog()}
严格按以下 JSON 输出，不要其他内容：
{{"results": [{{"companies": ["key", ...]}}, ...]}}
数组顺序与输入一致，每条新闻对应一个元素。"""


def _keyword_companies(article: dict) -> list[str]:
    haystack = " ".join(filter(None, [
        article.get("title", ""), article.get("title_zh", ""), article.get("summary_zh", ""),
    ]))
    return company_registry.keyword_tag(haystack)


def _assign_companies_batch(client: OpenAI, batch: list[dict]) -> list[list[str]] | None:
    """对一批已处理文章做"主体公司"判定，返回与 batch 等长的 key 列表；失败返回 None。"""
    parts = [
        f"[{i + 1}] 标题：{a.get('title_zh') or a.get('title', '')}\n摘要：{a.get('summary_zh', '')}"
        for i, a in enumerate(batch)
    ]
    user = f"请判断以下 {len(batch)} 条新闻各自主要讲述的公司：\n\n" + "\n\n".join(parts)
    valid = company_registry.valid_keys()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model="deepseek-v4-pro",
                max_tokens=4096,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": COMPANY_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                extra_body={"enable_thinking": False},
            )
            results = json.loads(response.choices[0].message.content).get("results", [])
            if isinstance(results, list) and len(results) == len(batch):
                return [
                    [k for k in (r.get("companies", []) if isinstance(r, dict) else []) if k in valid]
                    for r in results
                ]
            logger.warning(f"Companies backfill shape mismatch (attempt {attempt}/{MAX_RETRIES})")
        except Exception as e:
            logger.warning(f"Companies backfill error (attempt {attempt}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    return None


def backfill_companies(date_str: str | None = None):
    """对最近一天的存档补齐 companies 字段（LLM 主体公司判定，无 key 时关键词兜底）。

    只处理尚未带 companies 的文章；已带的（新流程已写入或上轮已补齐）直接跳过，
    因此部署后仅首轮有一次额外开销，之后基本是空操作。
    """
    if not ARTICLES_DIR.exists():
        return
    if date_str:
        path = ARTICLES_DIR / f"{date_str}.json"
        if not path.exists():
            return
    else:
        files = sorted(ARTICLES_DIR.glob("*.json"), reverse=True)
        if not files:
            return
        path = files[0]

    with open(path, "r", encoding="utf-8") as f:
        articles = json.load(f)
    todo = [a for a in articles if not isinstance(a.get("companies"), list)]
    if not todo:
        return

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        for a in todo:
            a["companies"] = _keyword_companies(a)
        logger.info(f"Backfilled companies (keyword) for {len(todo)} articles in {path.name}")
    else:
        client = OpenAI(api_key=api_key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        for i in range(0, len(todo), BATCH_SIZE):
            batch = todo[i:i + BATCH_SIZE]
            out = _assign_companies_batch(client, batch)
            for j, a in enumerate(batch):
                a["companies"] = out[j] if out is not None else _keyword_companies(a)
        logger.info(f"Backfilled companies (LLM) for {len(todo)} articles in {path.name}")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)


def load_processed_index(limit_files: int = 3) -> dict[str, dict]:
    """从最近若干个按日存档文件构建 {id: article} 索引。

    用于在处理前识别"已处理过"的文章，避免重复翻译/摘要。
    """
    index: dict[str, dict] = {}
    if not ARTICLES_DIR.exists():
        return index
    for path in sorted(ARTICLES_DIR.glob("*.json"), reverse=True)[:limit_files]:
        with open(path, "r", encoding="utf-8") as f:
            for a in json.load(f):
                index[a["id"]] = a
    return index


def save_processed(articles: list[dict], date_str: str):
    data_dir = ARTICLES_DIR
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
