"""中国核心统计数据抓取（World Bank 为主，FRED 为可选月度补充）。

为何不用国家统计局官方接口：data.stats.gov.cn 的数据 API 在 GitHub Actions（海外 IP）
被反爬 WAF 拦截（首页 200、easyquery 403），无法稳定直连。World Bank 开放数据无需 key、
稳定可达，数字多源自各国官方（含 NBS），适合做宏观面板；FRED 提供月度时效，但需要免费
API key（设为 FRED_API_KEY 后自动启用），且部分中国月度序列可能停更，故全部按可失败处理。

产出 data/stats/latest.json，供 stats_generator 渲染统计页。
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

STATS_DIR = Path(__file__).parent.parent / "data" / "stats"
CST = timezone(timedelta(hours=8))

WB_BASE = "https://api.worldbank.org/v2/country/CHN/indicator"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 指标注册表。group 对应页面四大板块；wb 为 World Bank 指标代码；
# fmt 控制展示：div 缩放系数、unit 单位、dec 小数位；better 表示"越大越好(up)还是越小越好(down)"，
# 用于给同比变化上色。fred 为可选的月度序列代码（设了 FRED_API_KEY 才会尝试）。
INDICATORS = [
    # —— 经济大盘 ——
    {"key": "gdp", "name_zh": "国内生产总值 (GDP)", "group": "经济大盘",
     "wb": "NY.GDP.MKTP.CD", "fmt": {"div": 1e12, "unit": "万亿美元", "dec": 2}, "better": "up"},
    {"key": "gdp_growth", "name_zh": "GDP 实际增长率", "group": "经济大盘",
     "wb": "NY.GDP.MKTP.KD.ZG", "fmt": {"div": 1, "unit": "%", "dec": 1}, "better": "up"},
    {"key": "gdp_pc", "name_zh": "人均 GDP", "group": "经济大盘",
     "wb": "NY.GDP.PCAP.CD", "fmt": {"div": 1, "unit": "美元", "dec": 0}, "better": "up"},
    {"key": "cpi", "name_zh": "通货膨胀 (CPI 年率)", "group": "经济大盘",
     "wb": "FP.CPI.TOTL.ZG", "fmt": {"div": 1, "unit": "%", "dec": 1}, "better": "down",
     "fred": "CPALTT01CNM659N"},
    # —— 就业 ——
    {"key": "unemployment", "name_zh": "失业率 (ILO 建模)", "group": "就业",
     "wb": "SL.UEM.TOTL.ZS", "fmt": {"div": 1, "unit": "%", "dec": 1}, "better": "down"},
    {"key": "unemployment_youth", "name_zh": "青年失业率 (15-24 岁)", "group": "就业",
     "wb": "SL.UEM.1524.ZS", "fmt": {"div": 1, "unit": "%", "dec": 1}, "better": "down"},
    {"key": "labor_force", "name_zh": "劳动力总数", "group": "就业",
     "wb": "SL.TLF.TOTL.IN", "fmt": {"div": 1e8, "unit": "亿人", "dec": 2}, "better": "up"},
    # —— 民生 / 消费 ——
    {"key": "consumption", "name_zh": "居民消费支出", "group": "民生/消费",
     "wb": "NE.CON.PRVT.CD", "fmt": {"div": 1e12, "unit": "万亿美元", "dec": 2}, "better": "up"},
    {"key": "gni_pc", "name_zh": "人均国民总收入 (GNI)", "group": "民生/消费",
     "wb": "NY.GNP.PCAP.CD", "fmt": {"div": 1, "unit": "美元", "dec": 0}, "better": "up"},
    {"key": "population", "name_zh": "年末总人口", "group": "民生/消费",
     "wb": "SP.POP.TOTL", "fmt": {"div": 1e8, "unit": "亿人", "dec": 2}, "better": "up"},
    # —— 投资 / 地产 / 外贸 ——
    {"key": "investment", "name_zh": "资本形成总额占 GDP 比", "group": "投资/地产/外贸",
     "wb": "NE.GDI.TOTL.ZS", "fmt": {"div": 1, "unit": "%", "dec": 1}, "better": "up"},
    {"key": "exports", "name_zh": "货物与服务出口", "group": "投资/地产/外贸",
     "wb": "NE.EXP.GNFS.CD", "fmt": {"div": 1e12, "unit": "万亿美元", "dec": 2}, "better": "up"},
    {"key": "imports", "name_zh": "货物与服务进口", "group": "投资/地产/外贸",
     "wb": "NE.IMP.GNFS.CD", "fmt": {"div": 1e12, "unit": "万亿美元", "dec": 2}, "better": "up"},
]

GROUP_ORDER = ["经济大盘", "就业", "民生/消费", "投资/地产/外贸"]

WB_START, WB_END = 2010, 2026


def _fetch_wb(code: str, timeout: int = 30) -> list[dict]:
    """拉取某 World Bank 指标的中国时间序列，升序返回 [{date, value}]（剔除空值）。"""
    params = {"format": "json", "per_page": "1000", "date": f"{WB_START}:{WB_END}"}
    r = requests.get(f"{WB_BASE}/{code}", params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []
    pts = [{"date": row["date"], "value": float(row["value"])}
           for row in rows if row.get("value") is not None]
    pts.sort(key=lambda p: p["date"])
    return pts


def _fetch_fred(series_id: str, api_key: str, timeout: int = 30) -> list[dict]:
    """拉取 FRED 月度序列，升序返回 [{date, value}]（剔除缺测点 '.'）。"""
    params = {
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "observation_start": f"{WB_START}-01-01",
    }
    r = requests.get(FRED_BASE, params=params, timeout=timeout)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    pts = []
    for o in obs:
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                pts.append({"date": o["date"], "value": float(v)})
            except ValueError:
                continue
    return pts


def build_dataset() -> dict:
    """抓取全部指标，组装为 {generated_at, indicators:[...]}。单个指标失败不影响其余。"""
    fred_key = os.environ.get("FRED_API_KEY")
    indicators = []
    for spec in INDICATORS:
        item = {k: spec[k] for k in ("key", "name_zh", "group", "fmt", "better")}
        item["source"] = "World Bank"
        item["freq"] = "年度"
        try:
            pts = _fetch_wb(spec["wb"])
        except Exception as e:
            logger.warning(f"[stats] WB fetch failed for {spec['key']} ({spec['wb']}): {e}")
            pts = []

        # 可选：FRED 月度序列覆盖（更高时效），失败则保留 World Bank 年度
        if fred_key and spec.get("fred"):
            try:
                fred_pts = _fetch_fred(spec["fred"], fred_key)
                if fred_pts:
                    pts = fred_pts
                    item["source"] = "FRED (OECD)"
                    item["freq"] = "月度"
            except Exception as e:
                logger.warning(f"[stats] FRED fetch failed for {spec['key']} ({spec['fred']}): {e}")

        item["points"] = pts
        if pts:
            item["latest"] = pts[-1]
            item["prev"] = pts[-2] if len(pts) > 1 else None
        else:
            item["latest"] = None
            item["prev"] = None
        indicators.append(item)

    ok = sum(1 for i in indicators if i["points"])
    logger.info(f"[stats] built dataset: {ok}/{len(indicators)} indicators have data "
                f"(FRED {'on' if fred_key else 'off'})")
    return {
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M CST"),
        "generated_date": datetime.now(CST).strftime("%Y-%m-%d"),
        "indicators": indicators,
    }


def save_dataset(dataset: dict) -> Path:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    path = STATS_DIR / "latest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    return path


def load_dataset() -> dict | None:
    path = STATS_DIR / "latest.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def refresh_stats(max_age_hours: float = 20.0) -> dict | None:
    """按需刷新：本地已有当天数据则直接复用，否则抓取并保存。无网络/全失败时回退旧数据。"""
    existing = load_dataset()
    today = datetime.now(CST).strftime("%Y-%m-%d")
    if existing and existing.get("generated_date") == today:
        return existing

    try:
        dataset = build_dataset()
    except Exception as e:
        logger.warning(f"[stats] build_dataset crashed: {e}")
        return existing

    if not any(i["points"] for i in dataset["indicators"]):
        logger.warning("[stats] no indicator returned data; keeping previous dataset")
        return existing or dataset

    save_dataset(dataset)
    return dataset


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ds = build_dataset()
    for ind in ds["indicators"]:
        latest = ind["latest"]
        tail = f'{latest["date"]}={latest["value"]}' if latest else "无数据"
        print(f'  [{ind["group"]}] {ind["name_zh"]}: {len(ind["points"])} 点, 最新 {tail}')
