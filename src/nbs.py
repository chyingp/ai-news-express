"""国家统计局（NBS）数据抓取与可达性探测。

数据来自 data.stats.gov.cn 的 easyquery.htm 接口（返回 JSON）。该接口对请求头/Cookie
较敏感，历史上对海外 IP 也不稳定，因此正式接入前先用本模块的 __main__ 在 CI 里探测：
  python src/nbs.py
会打印接口可达性、SSL 校验是否需关闭、指标树顶层节点，以及若干已知指标的最新取值，
据此决定数据源是否可用、并校准真实的指标 zb 编码。
"""
import json
import logging
import time

import requests
import urllib3

logger = logging.getLogger(__name__)

BASE = "https://data.stats.gov.cn/easyquery.htm"
HOME = "https://data.stats.gov.cn/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": HOME,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 数据库代码：hgyd=月度, hgjd=季度, hgnd=年度
DB_MONTH, DB_QUARTER, DB_YEAR = "hgyd", "hgjd", "hgnd"

# 探测用的高置信度指标（编码若有偏差，靠 getTree 输出校准）
PROBE_INDICATORS = [
    ("CPI·居民消费价格指数(上年同月=100)", "A01010101", DB_MONTH),
    ("PPI·工业生产者出厂价格(上年同月=100)", "A01080101", DB_MONTH),
    ("社会消费品零售总额·当期值", "A070101", DB_MONTH),
    ("规模以上工业增加值·当期同比", "A020102", DB_MONTH),
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def query_data(session: requests.Session, zb_code: str, dbcode: str = DB_MONTH,
               verify: bool = True, timeout: int = 25) -> dict:
    """按指标编码拉取一条时间序列（指标×时间）。"""
    params = {
        "m": "QueryData",
        "dbcode": dbcode,
        "rowcode": "zb",
        "colcode": "sj",
        "wds": "[]",
        "dfwds": json.dumps([{"wdcode": "zb", "valuecode": zb_code}], ensure_ascii=False),
        "k1": str(int(time.time() * 1000)),
    }
    r = session.get(BASE, params=params, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r.json()


def get_tree(session: requests.Session, node_id: str = "zb", dbcode: str = DB_MONTH,
             verify: bool = True, timeout: int = 25) -> list:
    """获取指标树某节点的子节点（用于发现/校准真实指标编码）。"""
    data = {"id": node_id, "dbcode": dbcode, "wdcode": "zb", "m": "getTree"}
    r = session.post(BASE, data=data, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r.json()


def latest_points(query_json: dict, n: int = 3) -> list[tuple[str, str]]:
    """从 QueryData 结果里取最近 n 个非空 (时间, 值)。"""
    nodes = query_json.get("returndata", {}).get("datanodes", [])
    points = []
    for node in nodes:
        sj = next((w["valuecode"] for w in node.get("wds", []) if w["wdcode"] == "sj"), "")
        d = node.get("data", {})
        if d.get("hasdata") and sj:
            points.append((sj, d.get("strdata") or str(d.get("data", ""))))
    points.sort(key=lambda x: x[0])
    return points[-n:]


def _probe():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=== NBS 可达性探测 ===")
    session = make_session()

    # 先访问首页种 Cookie（部分接口依赖 JSESSIONID）
    for verify in (True, False):
        try:
            r = session.get(HOME, timeout=25, verify=verify)
            print(f"[home verify={verify}] HTTP {r.status_code}, cookies={list(session.cookies.keys())}")
            break
        except Exception as e:
            print(f"[home verify={verify}] FAILED: {e!r}")

    working_verify = None
    for verify in (True, False):
        if not verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            data = query_data(session, "A01010101", DB_MONTH, verify=verify)
            rc = data.get("returncode")
            pts = latest_points(data)
            print(f"[query verify={verify}] HTTP OK, returncode={rc}, latest CPI={pts}")
            if rc == 200:
                working_verify = verify
                break
        except Exception as e:
            print(f"[query verify={verify}] FAILED: {e!r}")

    if working_verify is None:
        print("结论：CI 无法访问国家统计局接口（被拦截/超时/SSL）。需改用其它数据源。")
        return

    print(f"\n=== 接口可用（verify={working_verify}）。指标树顶层（月度 hgyd）===")
    try:
        tree = get_tree(session, "zb", DB_MONTH, verify=working_verify)
        for node in tree:
            print(f"  {node.get('id')}\t{node.get('name')}\t(isParent={node.get('isParent')})")
    except Exception as e:
        print(f"  getTree FAILED: {e!r}")

    print("\n=== 各探测指标最新取值 ===")
    for name, code, db in PROBE_INDICATORS:
        try:
            data = query_data(session, code, db, verify=working_verify)
            print(f"  [{name}] {code}: {latest_points(data)}")
        except Exception as e:
            print(f"  [{name}] {code}: FAILED {e!r}")


if __name__ == "__main__":
    _probe()
