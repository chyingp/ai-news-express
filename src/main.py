import argparse
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

from fetcher import fetch_all
from processor import process_articles, save_processed
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
        generate_index()
        return

    logger.info(f"Fetched {len(articles)} articles, processing...")
    processed = process_articles(articles)

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    save_processed(processed, date_str)

    generate_index(processed)
    logger.info(f"=== Hourly check done: {len(processed)} articles ===")


def run_daily():
    logger.info("=== Daily digest started ===")

    articles = fetch_all()
    if articles:
        processed = process_articles(articles)
        date_str = datetime.now(CST).strftime("%Y-%m-%d")
        save_processed(processed, date_str)

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
