"""
指标计算与评级。

输入：history（升序 OHLCV 列表）+ snapshot（可选）
输出：综合得分(0-100)、五档评级、各维子分与关键指标。

评分口径（权重在 config.json 可调）：
  估值分 valuation ：当前收盘价在历史序列中的价格分位反向（越低估越高分）。
  技术分 technical  ：均线趋势 + 阶段动量 + RSI 位置。
  拥挤度 crowding  ：量比 + 短期涨幅过热 + 价位分位合成（0-100，越高越拥挤，反向计入）。
  风险分 risk      ：年化波动率与近一年最大回撤，越小越安全（得分越高）。

说明：免费端外接口拿不到 ETF 份额申赎、换手率、主力资金流，拥挤度为量价代理口径。
"""

import math


def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rolling_ma(values, n):
    """截至当日的滚动均线序列；不足 n 日的位置为 None。"""
    out = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(sum(values[i - n + 1:i + 1]) / n)
    return out


def _ema_series(values, n):
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _macd(closes):
    if len(closes) < 26:
        return None, None, None
    e12 = _ema_series(closes, 12)
    e26 = _ema_series(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = _ema_series(dif, 9)
    return dif[-1], dea[-1], (dif[-1] - dea[-1]) * 2


def _percentile_rank(last, closes):
    """当前值在历史序列中的分位，0=最低，1=最高。"""
    if not closes:
        return None
    n = len(closes)
    below = sum(1 for c in closes if c <= last)
    return below / n


def _max_drawdown(closes, window=250):
    seg = closes[-window:]
    peak = seg[0]
    mdd = 0.0
    for c in seg:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)
    return mdd  # 负值


def _annual_volatility(closes, window=250):
    seg = closes[-window:]
    if len(seg) < 2:
        return None
    rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def _volume_ratio(volumes, short=5, long=60):
    """近 short 日均量 / 近 long 日均量。"""
    vols = [v for v in volumes if v is not None]
    if len(vols) < short + 1:
        return None
    long = min(long, len(vols))
    avg_s = sum(vols[-short:]) / short
    avg_l = sum(vols[-long:]) / long
    if avg_l <= 0:
        return None
    return avg_s / avg_l


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _ret(closes, n):
    if len(closes) <= n or closes[-n - 1] in (None, 0):
        return None
    return closes[-1] / closes[-n - 1] - 1


def _crowding(closes, volumes, pct, ret20):
    """拥挤度 0-100，越高越拥挤。返回 (crowding, volume_ratio)。"""
    ratio = _volume_ratio(volumes)
    if ratio is None:
        vol_crowd = 50.0
    else:
        vol_crowd = _clamp((ratio - 0.8) / 1.4 * 100)

    if ret20 is None:
        mom_crowd = 50.0
    else:
        mom_crowd = _clamp(50 + ret20 / 0.15 * 50)

    price_crowd = (pct * 100) if pct is not None else 50.0

    crowding = 0.5 * vol_crowd + 0.3 * mom_crowd + 0.2 * price_crowd
    return round(_clamp(crowding), 1), round(ratio, 2) if ratio is not None else None


def score_item(history, cfg, cape_series=None, risk_free=None):
    """根据历史序列计算评级。history 为升序 OHLCV 列表。

    cape_series: 可选，Shiller CAPE 月度序列 [{"date","cape"}, ...]；
    提供时估值分改按 CAPE 历史分位计算（口径 basis=cape），否则用价格分位（basis=price）。
    risk_free: 可选，美债 10 年收益率（百分数），仅用于 CAPE 标的 ERP 参考展示。
    """
    if not history or len(history) < 20:
        return {
            "score": None, "rating": "数据不足",
            "signals": {"valuation": None, "valuation_basis": "price",
                        "technical": None, "crowding": None, "risk": None},
            "metrics": {"valuation_basis": "price", "cape": None, "cape_percentile": None,
                        "bias250": None, "bias_percentile": None, "valuation_trend": None,
                        "erp": None, "risk_free_rate": None},
            "confidence": "low",
            "note": "历史数据不足，无法完成评分。",
        }

    closes = [h["close"] for h in history if h["close"] is not None]
    volumes = [h.get("volume") for h in history]
    last = closes[-1]
    n = len(closes)

    # --- 估值：分位反向 60% + 年线乖离分位 40% + 趋势微调 ---
    pct = _percentile_rank(last, closes)
    pct_score = (1 - pct) * 100 if pct is not None else None
    valuation_basis = "price"
    cape_val = None
    cape_pct = None

    # 中枢乖离：优先 MA250，样本不足退化为 MA60
    bias250 = None
    bias_pct = None
    bias_score = 50.0
    bias_window = 250
    ma_ref = _sma(closes, 250)
    if ma_ref is None:
        ma_ref = _sma(closes, 60)
        bias_window = 60
    if ma_ref is not None:
        bias250 = last / ma_ref - 1
        ma_series = _rolling_ma(closes, bias_window)
        bias_series = [c / m - 1 for c, m in zip(closes, ma_series) if m]
        if len(bias_series) >= 2:
            bias_pct = sum(1 for b in bias_series if b <= bias250) / len(bias_series)
            bias_score = (1 - bias_pct) * 100

    # 趋势微调：当前乖离对比 60 日前；收窄（回落）加分，扩张（变贵）扣分，幅度 ±5
    trend_adj = 0.0
    trend_label = None
    if bias250 is not None and len(closes) >= bias_window + 60:
        ma_ago = _sma(closes[:-60], bias_window)
        if ma_ago:
            bias_ago = closes[-61] / ma_ago - 1
            delta = bias250 - bias_ago
            trend_adj = _clamp(-delta / 0.10 * 5, -5, 5)
            if delta < -0.005:
                trend_label = "回落"
            elif delta > 0.005:
                trend_label = "走阔"
            else:
                trend_label = "持平"

    if cape_series:
        cape_vals = [c.get("cape") for c in cape_series if c.get("cape") is not None]
        if len(cape_vals) >= 2:
            cape_val = cape_vals[-1]
            cape_pct = sum(1 for v in cape_vals if v <= cape_val) / len(cape_vals)
            pct_score = (1 - cape_pct) * 100
            valuation_basis = "cape"

    if pct_score is not None:
        valuation = _clamp(0.6 * pct_score + 0.4 * bias_score + trend_adj)
    else:
        valuation = None

    # ERP（仅 CAPE 口径展示，不进主分）：盈利收益率 1/CAPE − 无风险利率
    erp = None
    if valuation_basis == "cape" and cape_val and risk_free is not None:
        erp = (1 / cape_val - risk_free / 100) * 100

    # --- 技术面 ---
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    ma250 = _sma(closes, 250)
    rsi = _rsi(closes, 14)
    dif, dea, macd_bar = _macd(closes)
    ret20 = _ret(closes, 20)
    ret60 = _ret(closes, 60)

    trend = 0.0
    checks = 0
    if ma20:
        checks += 1
        trend += 1 if last > ma20 else 0
    if ma20 and ma60:
        checks += 1
        trend += 1 if ma20 > ma60 else 0
    if ma60 and ma250:
        checks += 1
        trend += 1 if ma60 > ma250 else 0
    if ma250:
        checks += 1
        trend += 1 if last > ma250 else 0
    trend_score = (trend / checks * 100) if checks else 50.0

    def mom_score(r):
        if r is None:
            return 50.0
        return _clamp(50 + r / 0.15 * 50)
    momentum_score = (mom_score(ret20) + mom_score(ret60)) / 2

    if rsi is None:
        rsi_score = 50.0
    elif rsi <= 30:
        rsi_score = 55.0
    elif rsi >= 75:
        rsi_score = 40.0
    else:
        rsi_score = _clamp(50 + (rsi - 50) * 1.2)

    technical = 0.5 * trend_score + 0.3 * momentum_score + 0.2 * rsi_score

    # --- 拥挤度（反向） ---
    crowding, volume_ratio = _crowding(closes, volumes, pct, ret20)

    # --- 风险面 ---
    vol = _annual_volatility(closes)
    mdd = _max_drawdown(closes)
    vol_score = _clamp(100 - (vol - 0.05) / 0.25 * 100) if vol is not None else 50.0
    dd_score = _clamp(100 - abs(mdd) / 0.30 * 100) if mdd is not None else 50.0
    risk = 0.5 * vol_score + 0.5 * dd_score

    # --- 四维综合（拥挤度反向：100 - crowding） ---
    w = cfg["weights"]
    confidence = "high" if n >= 250 else "medium"
    if n < 60:
        confidence = "low"

    parts = []
    if valuation is not None:
        parts.append((w["valuation"], valuation))
    parts.append((w["technical"], technical))
    parts.append((w["crowding"], 100 - crowding))
    parts.append((w["risk"], risk))
    wsum = sum(p[0] for p in parts)
    score = sum(wt * sc for wt, sc in parts) / wsum if wsum else None
    score = _clamp(score) if score is not None else None

    rating = _rating(score, cfg["thresholds"])

    metrics = {
        "valuation_basis": valuation_basis,
        "cape": round(cape_val, 2) if cape_val is not None else None,
        "cape_percentile": round(cape_pct * 100, 1) if cape_pct is not None else None,
        "price_percentile": round(pct * 100, 1) if pct is not None else None,
        "bias250": round(bias250 * 100, 1) if bias250 is not None else None,
        "bias_percentile": round(bias_pct * 100, 1) if bias_pct is not None else None,
        "valuation_trend": trend_label,
        "erp": round(erp, 2) if erp is not None else None,
        "risk_free_rate": round(risk_free, 2) if risk_free is not None else None,
        "volume_ratio_5_60": volume_ratio,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "ma250": round(ma250, 2) if ma250 else None,
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "macd_dif": round(dif, 3) if dif is not None else None,
        "macd_dea": round(dea, 3) if dea is not None else None,
        "macd_bar": round(macd_bar, 3) if macd_bar is not None else None,
        "ret20": round(ret20 * 100, 2) if ret20 is not None else None,
        "ret60": round(ret60 * 100, 2) if ret60 is not None else None,
        "volatility_1y": round(vol * 100, 1) if vol is not None else None,
        "max_drawdown_1y": round(mdd * 100, 1) if mdd is not None else None,
        "history_len": n,
    }

    note = None
    if confidence == "low":
        note = "历史样本不足，结论仅供参考。"

    return {
        "score": round(score, 1) if score is not None else None,
        "rating": rating,
        "signals": {
            "valuation": round(valuation, 1) if valuation is not None else None,
            "valuation_basis": valuation_basis,
            "technical": round(technical, 1),
            "crowding": crowding,
            "risk": round(risk, 1),
        },
        "metrics": metrics,
        "confidence": confidence,
        "note": note,
    }


def _rating(score, th):
    if score is None:
        return "数据不足"
    if score >= th["strong_buy"]:
        return "强烈买入"
    if score >= th["buy"]:
        return "买入"
    if score >= th["hold"]:
        return "观望"
    if score >= th["sell"]:
        return "卖出"
    return "强烈卖出"
