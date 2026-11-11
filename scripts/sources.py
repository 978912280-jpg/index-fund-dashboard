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


def sina_cn_history(symbol, datalen=750):
    """A 股 / ETF 日线。symbol 如 sh512890 / sz159905。"""
    if not symbol:
        return None
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_k=/"
           "CN_MarketDataService.getKLineData")
    r = _get(url, params={"symbol": symbol, "scale": 240, "ma": "no",
                          "datalen": min(datalen, 1023)}, timeout=12)
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
    return out[-750:] if out else None


# --------------------------------------------------------------------------
# Yahoo chart 兜底（海外市场；Actions 海外 runner 可达，本地国内网络可能不通）
# --------------------------------------------------------------------------

def yahoo_history(symbol):
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = _get(url, params={"range": "5y", "interval": "1d"}, timeout=12)
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
    return out[-750:] if out else None
