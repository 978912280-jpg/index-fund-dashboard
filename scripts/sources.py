"""
数据取数适配层。

统一输出：
  snapshot = {
    "price": float|None, "prev_close": float|None, "change_pct": float|None,
    "open": float|None, "high": float|None, "low": float|None,
    "ts": str|None, "source": str
  }
  history  = [ {"date":"YYYY-MM-DD","open":float,"high":float,"low":float,
                "close":float,"volume":float|None}, ... ]  # 时间升序

数据源优先级（在 build.py 中按标的类型编排）：
  国内指数/ETF : 同花顺官方 REST (需 HITHINK_API_KEY) -> 腾讯实时 / 新浪历史
  海外指数     : 腾讯实时 -> 新浪美股历史 -> Yahoo chart 兜底

注意：新浪/腾讯/东财/Yahoo 为站点前端或受限源，仅供个人研究；
合规级别与条款以各源官方说明为准。
"""

import os
import re
import json
import time
import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


def _get(url, params=None, headers=None, timeout=12, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers or UA,
                              timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < retries:
            time.sleep(1.5)
    return None


def _f(x):
    """宽松转 float，失败返回 None。"""
    if x is None:
        return None
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 同花顺官方 REST（fuyao.aicubes.cn）
# --------------------------------------------------------------------------

def _hithink_key(cfg):
    return os.environ.get(cfg["sources"].get("hithink_api_key_env", "HITHINK_API_KEY"), "").strip()


def _hithink_get(cfg, path, params):
    key = _hithink_key(cfg)
    if not key:
        return None
    base = cfg["sources"]["hithink_base"].rstrip("/")
    headers = dict(UA)
    headers["X-api-key"] = key
    r = _get(f"{base}{path}", params=params, headers=headers,
             timeout=cfg["sources"].get("timeout_sec", 12))
    if r is None:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if body.get("code") not in (0, None):
        return None
    return body.get("data")


def hithink_snapshot(item, cfg):
    """国内指数 / ETF 最新快照。返回 snapshot 或 None。"""
    if not item.get("hithink"):
        return None
    th = item["hithink"]
    if item["kind"] == "etf":
        path = "/fund/market/snapshot"
    else:
        path = "/a-share-index/prices/snapshot"
    data = _hithink_get(cfg, path, {"thscode": th})
    if not data or not data.get("item"):
        return None
    it = data["item"][0]
    price = _f(it.get("last_price") or it.get("close") or it.get("price"))
    prev = _f(it.get("prev_price") or it.get("prev_close"))
    chg = _f(it.get("price_change_ratio_pct") or it.get("change_ratio_pct"))
    if chg is None and price is not None and prev:
        chg = (price - prev) / prev * 100
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": chg,
        "open": _f(it.get("open_price") or it.get("open")),
        "high": _f(it.get("high_price") or it.get("high")),
        "low": _f(it.get("low_price") or it.get("low")),
        "ts": str(data.get("timestamp") or it.get("timestamp") or ""),
        "source": "hithink",
    }


def hithink_history(item, cfg, limit=750):
    """国内指数 / ETF 历史日线。返回 history 或 None。"""
    if not item.get("hithink"):
        return None
    th = item["hithink"]
    if item["kind"] == "etf":
        path = "/fund/market/historical"
    else:
        path = "/a-share-index/prices/historical"
    data = _hithink_get(cfg, path, {"thscode": th, "limit": limit})
    if not data:
        return None
    rows = data.get("item") or data.get("list") or []
    out = []
    for it in rows:
        d = it.get("date") or it.get("day") or it.get("trade_date")
        close = _f(it.get("close") or it.get("close_price"))
        if not d or close is None:
            continue
        out.append({
            "date": str(d)[:10],
            "open": _f(it.get("open") or it.get("open_price")),
            "high": _f(it.get("high") or it.get("high_price")),
            "low": _f(it.get("low") or it.get("low_price")),
            "close": close,
            "volume": _f(it.get("volume")),
        })
    out.sort(key=lambda x: x["date"])
    return out[-limit:] if out else None


# --------------------------------------------------------------------------
# 腾讯实时行情（国内 sh/sz，海外 us/hk）
# --------------------------------------------------------------------------

def tencent_realtime(tcode):
    if not tcode:
        return None
    r = _get(f"https://qt.gtimg.cn/q={tcode}", timeout=10)
    if r is None:
        return None
    r.encoding = "gbk"
    m = re.search(r'v_[^=]+="([^"]*)"', r.text)
    if not m:
        return None
    p = m.group(1).split("~")
    if len(p) < 6:
        return None
    def g(i):
        return _f(p[i]) if i < len(p) else None
    price, prev, open_ = g(3), g(4), g(5)
    chg = g(32)
    if chg is None and price is not None and prev:
        chg = (price - prev) / prev * 100
    return {
        "price": price,
        "prev_close": prev,
        "change_pct": chg,
        "open": open_,
        "high": g(33),
        "low": g(34),
        "ts": (p[30] if len(p) > 30 else "") or None,
        "source": "tencent",
    }


# --------------------------------------------------------------------------
# 新浪历史日线
# --------------------------------------------------------------------------

def _parse_jsonp(text):
    m = re.search(r"\((\[.*\]|\{.*\})\)\s*;?\s*$", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def sina_cn_history(symbol, datalen=1500):
    """A 股 / ETF 日线。symbol 如 sh512890 / sz159905。"""
    if not symbol:
        return None
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_k=/"
           "CN_MarketDataService.getKLineData")
    r = _get(url, params={"symbol": symbol, "scale": 240, "ma": "no",
                          "datalen": datalen}, timeout=12)
    if r is None:
        return None
    rows = _parse_jsonp(r.text)
    if not isinstance(rows, list):
        return None
    out = []
    for it in rows:
        close = _f(it.get("close"))
        if not it.get("day") or close is None:
            continue
        out.append({
            "date": it["day"],
            "open": _f(it.get("open")),
            "high": _f(it.get("high")),
            "low": _f(it.get("low")),
            "close": close,
            "volume": _f(it.get("volume")),
        })
    out.sort(key=lambda x: x["date"])
    return out[-datalen:] if out else None


def sina_us_history(symbol):
    """美股指数日线。symbol 如 .IXIC / .INX / .DJI。"""
    if not symbol:
        return None
    url = ("https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20_a=/"
           "US_MinKService.getDailyK")
    r = _get(url, params={"symbol": symbol}, timeout=12)
    if r is None:
        return None
    rows = _parse_jsonp(r.text)
    if not isinstance(rows, list):
        return None
    out = []
    for it in rows:
        close = _f(it.get("c"))
        if not it.get("d") or close is None:
            continue
        out.append({
            "date": it["d"],
            "open": _f(it.get("o")),
            "high": _f(it.get("h")),
            "low": _f(it.get("l")),
            "close": close,
            "volume": _f(it.get("v")),
        })
    out.sort(key=lambda x: x["date"])
    return out or None


# --------------------------------------------------------------------------
# 天天基金 · 场外开放式基金单位净值历史
# --------------------------------------------------------------------------

def eastmoney_fund_history(code):
    """场外基金单位净值历史。code 如 008163。返回 history 或 None。

    场外基金无盘中 OHLC 与成交量，开/高/低/收均取单位净值，volume 置 None。
    """
    if not code:
        return None
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    headers = dict(UA)
    headers["Referer"] = "https://fund.eastmoney.com/"
    r = _get(url, headers=headers, timeout=12)
    if r is None:
        return None
    text = r.text
    m = re.search(r"Data_netWorthTrend\s*=\s*", text)
    if not m:
        return None
    i = m.end()
    depth, instr, esc, end = 0, False, False, None
    for j in range(i, len(text)):
        c = text[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
    if end is None:
        return None
    try:
        arr = json.loads(text[i:end])
    except ValueError:
        return None
    out = []
    for it in arr:
        ts = it.get("x")
        nav = _f(it.get("y"))
        if ts is None or nav is None:
            continue
        d = time.strftime("%Y-%m-%d", time.gmtime(ts / 1000))
        out.append({"date": d, "open": nav, "high": nav,
                    "low": nav, "close": nav, "volume": None})
    out.sort(key=lambda x: x["date"])
    return out or None


# --------------------------------------------------------------------------
# Yahoo chart 兜底（海外市场；Actions 海外 runner 可达，本地国内网络可能不通）
# --------------------------------------------------------------------------

def yahoo_history(symbol):
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _get(url, params={"range": "6y", "interval": "1d"}, timeout=12)
    if r is None:
        return None
    try:
        body = r.json()
        res = body["chart"]["result"][0]
    except (ValueError, KeyError, IndexError):
        return None
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []
    out = []
    for i, t in enumerate(ts):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        out.append({
            "date": time.strftime("%Y-%m-%d", time.gmtime(t)),
            "open": _f(opens[i]) if i < len(opens) else None,
            "high": _f(highs[i]) if i < len(highs) else None,
            "low": _f(lows[i]) if i < len(lows) else None,
            "close": _f(close),
            "volume": _f(vols[i]) if i < len(vols) else None,
        })
    out.sort(key=lambda x: x["date"])
    return out or None


def tnx_yield():
    """美债 10 年期国债收益率（百分数，如 4.35 表示 4.35%）。

    仅用于 CAPE 标的的股权风险溢价（ERP）参考展示；不可用返回 None。
    走 Yahoo chart，需 Actions 海外 runner 可达。
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX"
    r = _get(url, params={"range": "1mo", "interval": "1d"}, timeout=12)
    if r is None:
        return None
    try:
        res = r.json()["chart"]["result"][0]
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        closes = [c for c in closes if c is not None]
        return _f(closes[-1]) if closes else None
    except (ValueError, KeyError, IndexError, TypeError):
        return None


# --------------------------------------------------------------------------
# Shiller S&P 500 月度 CAPE（席勒市盈率，长历史 1871 起）
# --------------------------------------------------------------------------

SHILLER_XLS_URL = ("https://img1.wsimg.com/blobby/go/e5e77e0b-59d1-44d9-ab25-4763ac982e53/"
                   "downloads/1449043e-a1ed-4e2e-8e9c-7b9a2a064493/ie_data.xls")
SHILLER_XLS_FALLBACK = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"


def shiller_cape():
    """Shiller S&P 500 月度 CAPE 序列。

    返回 [{"date":"YYYY-MM-01","cape":float}, ...] 时间升序；不可用时返回 None。
    仅适用于标普500 及其跟踪 ETF；其余指数无对应长历史免费源。
    """
    try:
        import xlrd
    except ImportError:
        return None

    raw = None
    for url in (SHILLER_XLS_URL, SHILLER_XLS_FALLBACK):
        r = _get(url, timeout=15)
        if r is not None and r.content:
            raw = r.content
            break
    if not raw:
        return None

    try:
        wb = xlrd.open_workbook(file_contents=raw)
        sh = wb.sheet_by_name("Data")
    except Exception:
        return None

    out = []
    for r in range(6, sh.nrows):
        t = sh.cell_value(r, 0)
        cape = _f(sh.cell_value(r, 12))  # M 列：Cyclically Adjusted Price Ratio
        if not isinstance(t, float) or cape is None:
            continue
        y = int(t)
        m = round((t - y) * 100)
        if not (1 <= m <= 12):
            continue
        out.append({"date": f"{y:04d}-{m:02d}-01", "cape": cape})
    out.sort(key=lambda x: x["date"])
    return out or None
