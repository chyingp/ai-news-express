import argparse
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_all
from processor import process_articles, save_processed, load_processed_index, backfill_companies
from generator import generate_index, generate_daily

CST = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def run_hourly():
    logger.info("=== Hourly check started ===")

    articles = fetch_all()
    if not articles:
        logger.info("No new articles, skipping")
        backfill_companies()
        generate_index()
        return

    logger.info(f"Fetched {len(articles)} articles, processing...")
    processed = process_articles(articles, known=load_processed_index())

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    save_processed(processed, date_str)

    # 对最近一天的存档补齐"主体公司"标注（新文章已在处理阶段带上，此处只兜旧的）。
    backfill_companies()

    # 从存档全量重建首页（最近数日），而非只用本次新抓到的文章，
    # 否则首页会被压缩成仅剩这一批新文章。
    generate_index()
    logger.info(f"=== Hourly check done: {len(processed)} articles ===")


def run_daily():
    logger.info("=== Daily digest started ===")

    articles = fetch_all()
    if articles:
        processed = process_articles(articles, known=load_processed_index())
        date_str = datetime.now(CST).strftime("%Y-%m-%d")
        save_processed(processed, date_str)

    backfill_companies()
    generate_daily()
    logger.info("=== Daily digest done ===")


def main():
    parser = argparse.ArgumentParser(description="AI 新闻速递")
    parser.add_argument(
        "--mode",
        choices=["hourly", "daily"],
        default="hourly",
        help="hourly: 增量抓取+更新首页; daily: 生成每日汇总",
    )
    args = parser.parse_args()

    if args.mode == "hourly":
        run_hourly()
    elif args.mode == "daily":
        run_daily()


if __name__ == "__main__":
    main()
