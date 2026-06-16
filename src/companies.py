"""巨头企业注册表：单一数据源为 config/sources.yaml 的 companies 段。

- processor 用它构造 LLM"主体公司判定"的候选清单，并校验模型返回的 key；
- generator 用它做关键词兜底匹配（旧文章/无 key 时）与首页筛选条的展示名。
"""
import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).parent.parent / "config" / "sources.yaml"

_RAW: list[dict] | None = None
_PATTERNS: list[dict] | None = None


def load_companies() -> list[dict]:
    """读取企业表 [{key, label, aliases}]，按 sources.yaml 中的声明顺序返回。"""
    global _RAW
    if _RAW is None:
        try:
            with open(SOURCES_FILE, "r", encoding="utf-8") as f:
                _RAW = (yaml.safe_load(f) or {}).get("companies", [])
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f"Failed to load companies from {SOURCES_FILE}: {e}")
            _RAW = []
    return _RAW


def valid_keys() -> set[str]:
    return {c["key"] for c in load_companies()}


def labels() -> dict[str, str]:
    return {c["key"]: c.get("label", c["key"]) for c in load_companies()}


def prompt_catalog() -> str:
    """供 LLM 选择的候选清单，形如 'openai(OpenAI)、anthropic(Anthropic)、...'。"""
    return "、".join(f'{c["key"]}({c.get("label", c["key"])})' for c in load_companies())


def compiled_patterns() -> list[dict]:
    """关键词兜底用的预编译正则：ASCII 别名按词边界、中文按子串。"""
    global _PATTERNS
    if _PATTERNS is None:
        pats = []
        for c in load_companies():
            parts = []
            for alias in c.get("aliases", []):
                esc = re.escape(alias)
                if alias.isascii():
                    parts.append(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])")
                else:
                    parts.append(esc)
            if parts:
                pats.append({
                    "key": c["key"],
                    "pattern": re.compile("|".join(parts), re.IGNORECASE),
                })
        _PATTERNS = pats
    return _PATTERNS


def keyword_tag(text: str) -> list[str]:
    """关键词兜底：返回文本中提及的企业 key（召回优先，可能含陪衬式提及）。"""
    return [c["key"] for c in compiled_patterns() if c["pattern"].search(text)]
