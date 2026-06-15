"""一次性补翻历史数据。

把 data/articles/*.json 中未翻译（title_zh 为空，即此前走了启发式降级）的文章
重新交给 AI 处理并回填，然后重建受影响日期的日报页面与首页。

用法：
    cd src && python backfill.py             # 处理所有日期
    cd src && python backfill.py 2026-06-15  # 只处理指定日期

需要环境变量 DASHSCOPE_API_KEY；未设置时直接退出，不会用降级覆盖原数据。
"""
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

import processor
from generator import generate_daily, generate_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

DATA_DIR = Path(__file__).parent.parent / "data" / "articles"


def _needs_translation(article: dict) -> bool:
    return not article.get("title_zh")


def backfill_file(client: OpenAI, path: Path) -> int:
    with open(path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    todo = [a for a in articles if _needs_translation(a)]
    if not todo:
        logger.info(f"{path.name}: nothing to backfill")
        return 0

    logger.info(f"{path.name}: {len(todo)} untranslated articles")
    fixed = 0
    for i in range(0, len(todo), processor.BATCH_SIZE):
        batch = todo[i:i + processor.BATCH_SIZE]
        fixed += processor.process_batch_with_fallback(client, batch)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    logger.info(f"{path.name}: backfilled {fixed}/{len(todo)} via AI")
    return fixed


def main() -> int:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        logger.error("DASHSCOPE_API_KEY not set; cannot translate. Aborting.")
        return 1

    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    if len(sys.argv) > 1 and sys.argv[1].strip():
        files = [DATA_DIR / f"{sys.argv[1].strip()}.json"]
    else:
        files = sorted(DATA_DIR.glob("*.json"))

    total = 0
    touched_dates = []
    for path in files:
        if not path.exists():
            logger.warning(f"{path} not found, skipping")
            continue
        n = backfill_file(client, path)
        if n:
            total += n
            touched_dates.append(path.stem)

    if touched_dates:
        for date_str in touched_dates:
            generate_daily(date_str)
        generate_index()
        logger.info(f"Rebuilt pages for {len(touched_dates)} date(s)")
    else:
        logger.info("No pages rebuilt (nothing translated)")

    logger.info(f"Backfill done: {total} articles translated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
