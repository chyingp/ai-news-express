"""中国核心经济数据页生成：指标卡（含内联 SVG 趋势图）+ 分板块对比表。

纯静态渲染，趋势图为服务端生成的内联 SVG，页面零 JS 依赖，适配 GitHub Pages。
数据来自 stats_fetcher（World Bank / 可选 FRED），渲染到 output/stats/index.html。
"""
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import stats_fetcher

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
OUTPUT_DIR = PROJECT_ROOT / "output"


def _fmt(value: float | None, fmt: dict) -> str:
    """按指标的 fmt 配置缩放并格式化为展示字符串。"""
    if value is None:
        return "—"
    scaled = value / fmt.get("div", 1)
    dec = fmt.get("dec", 1)
    unit = fmt.get("unit", "")
    num = f"{scaled:,.{dec}f}"
    if unit == "%":
        return f"{num}%"
    return f"{num} {unit}" if unit else num


def _change(latest: dict | None, prev: dict | None, fmt: dict, better: str) -> dict | None:
    """计算同比/环比变化：百分比类指标用百分点差，其余用变化率。返回方向与配色。"""
    if not latest or not prev or prev["value"] in (0, None):
        return None
    is_pct = fmt.get("unit") == "%"
    if is_pct:
        delta = latest["value"] - prev["value"]
        text = f"{delta:+.1f}pp"
    else:
        delta = (latest["value"] - prev["value"]) / abs(prev["value"]) * 100
        text = f"{delta:+.1f}%"
    if abs(delta) < 1e-9:
        tone = "flat"
    else:
        rising = delta > 0
        good = (better == "up" and rising) or (better == "down" and not rising)
        tone = "good" if good else "bad"
    return {"text": text, "tone": tone, "arrow": "▲" if delta > 0 else ("▼" if delta < 0 else "■"),
            "from_date": prev["date"]}


def _sparkline(points: list[dict], width: int = 168, height: int = 44, pad: int = 4) -> str:
    """把最近若干个点画成内联 SVG 折线（带末端圆点），无数据返回空串。"""
    pts = points[-16:]
    if len(pts) < 2:
        return ""
    vals = [p["value"] for p in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(pts)
    coords = []
    for i, v in enumerate(vals):
        x = pad + (width - 2 * pad) * i / (n - 1)
        y = pad + (height - 2 * pad) * (1 - (v - lo) / span)
        coords.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad},{height - pad} " + line + f" {width - pad},{height - pad}"
    ex, ey = coords[-1]
    rising = vals[-1] >= vals[0]
    color = "#16a34a" if rising else "#dc2626"
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon points="{area}" fill="{color}" opacity="0.08"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="2.4" fill="{color}"/>'
        f'</svg>'
    )


def _build_view(dataset: dict) -> list[dict]:
    """把数据集整理为按板块分组的视图：每板块含指标卡数据 + 对比表（列为近 6 年）。"""
    by_group: dict[str, list[dict]] = {g: [] for g in stats_fetcher.GROUP_ORDER}
    for ind in dataset.get("indicators", []):
        card = {
            "name_zh": ind["name_zh"],
            "source": ind["source"],
            "freq": ind["freq"],
            "latest_value": _fmt(ind["latest"]["value"], ind["fmt"]) if ind["latest"] else "—",
            "latest_date": ind["latest"]["date"] if ind["latest"] else "",
            "change": _change(ind["latest"], ind["prev"], ind["fmt"], ind["better"]),
            "spark": _sparkline(ind["points"]),
            "fmt": ind["fmt"],
            "points": ind["points"],
        }
        by_group.setdefault(ind["group"], []).append(card)

    groups = []
    for name in stats_fetcher.GROUP_ORDER:
        cards = by_group.get(name, [])
        if not cards:
            continue
        # 对比表列：本板块所有指标里出现过的最近 6 个时间点（并集，降序）
        dates = sorted({p["date"] for c in cards for p in c["points"]}, reverse=True)[:6]
        dates = list(reversed(dates))
        rows = []
        for c in cards:
            lookup = {p["date"]: p["value"] for p in c["points"]}
            rows.append({
                "name_zh": c["name_zh"],
                "cells": [(_fmt(lookup[d], c["fmt"]) if d in lookup else "—") for d in dates],
            })
        groups.append({"name": name, "cards": cards, "table_dates": dates, "table_rows": rows})
    return groups


def generate_stats() -> bool:
    """刷新数据并渲染统计页到 output/stats/index.html。无任何数据时跳过。"""
    dataset = stats_fetcher.refresh_stats()
    if not dataset or not any(i["points"] for i in dataset.get("indicators", [])):
        logger.warning("[stats] no dataset available, skipping stats page")
        return False

    groups = _build_view(dataset)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    html = env.get_template("stats.html").render(
        groups=groups,
        generated_at=dataset.get("generated_at", ""),
        indicator_count=sum(1 for i in dataset["indicators"] if i["points"]),
    )

    out_dir = OUTPUT_DIR / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "index.html", "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"[stats] generated stats page with {len(groups)} groups")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_stats()
