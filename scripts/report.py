"""
每日规则化总结报告生成。

基于已算出的四维评分与关键指标，对标的池做分组归纳与要点提炼。
全部结论来自既有指标的规则化拼接，不做主观预测，不构成投资建议。

输出结构（写入 dashboard.json 的 report 字段）：
  generated_at      报告生成时间（ISO）
  summary           总数/有效评分/平均综合分/涨跌家数/评级分布/整体情绪
  groups            positive（买入档）/ neutral（观望）/ cautious（卖出档）标的清单
  bullets           估值、技术、拥挤、风险等维度的极端值要点
  category_summary 各分类平均评分与最强标的
"""

POSITIVE = {"强烈买入", "买入"}
CAUTIOUS = {"卖出", "强烈卖出"}


def _reason(it):
    """根据指标拼一句规则化依据（不含买卖建议措辞）。"""
    m = it.get("metrics") or {}
    s = it.get("signals") or {}
    parts = []

    pct = m.get("price_percentile")
    if pct is not None:
        if pct <= 30:
            parts.append(f"价格处于历史低位（分位 {pct:.1f}%）")
        elif pct >= 70:
            parts.append(f"价格处于历史高位（分位 {pct:.1f}%）")
        else:
            parts.append(f"价格处于历史中位区间（分位 {pct:.1f}%）")

    ret20 = m.get("ret20")
    if ret20 is not None:
        if ret20 >= 5:
            parts.append("近20日动量偏强")
        elif ret20 <= -5:
            parts.append("近20日动量偏弱")

    rsi = m.get("rsi14")
    if rsi is not None:
        if rsi >= 70:
            parts.append("RSI 处于超买区")
        elif rsi <= 30:
            parts.append("RSI 处于偏弱区")

    crowd = s.get("crowding")
    if crowd is not None:
        if crowd >= 65:
            parts.append("短期成交偏拥挤")
        elif crowd <= 35:
            parts.append("成交相对清淡")

    mdd = m.get("max_drawdown_1y")
    if mdd is not None:
        if mdd >= -10:
            parts.append("近一年回撤控制较好")
        elif mdd <= -25:
            parts.append("近一年回撤较大")

    return "；".join(parts) if parts else "指标信号中性"


def _mood(avg):
    if avg is None:
        return "有效样本不足，暂不评价"
    if avg >= 60:
        return "整体偏积极"
    if avg >= 50:
        return "中性偏强"
    if avg >= 40:
        return "中性"
    if avg >= 30:
        return "中性偏弱"
    return "整体偏谨慎"


def _pick(it):
    return {
        "code": it["code"],
        "name": it["name"],
        "score": it.get("score"),
        "rating": it.get("rating"),
        "change_pct": it.get("change_pct"),
        "reason": _reason(it),
    }


def _extreme(items, get_val, label, fmt, high=True):
    cand = [x for x in items if get_val(x) is not None]
    if not cand:
        return None
    x = sorted(cand, key=get_val, reverse=high)[0]
    return f"{label}：{x['name']}（{fmt(get_val(x))}）"


def build_report(items, cfg, now_iso):
    valid = [x for x in items if x.get("score") is not None]
    no_data = [x for x in items if x.get("score") is None]

    scores = [x["score"] for x in valid]
    avg = round(sum(scores) / len(scores), 1) if scores else None

    def _chg(x):
        v = x.get("change_pct")
        return v if v is not None else 0
    ups = [x for x in valid if _chg(x) > 0]
    downs = [x for x in valid if _chg(x) < 0]
    flats = [x for x in valid if _chg(x) == 0]

    by_rating = {}
    for x in items:
        r = x.get("rating") or "未知"
        by_rating[r] = by_rating.get(r, 0) + 1

    positive = [_pick(x) for x in valid if x["rating"] in POSITIVE]
    neutral = [_pick(x) for x in valid if x["rating"] == "观望"]
    cautious = [_pick(x) for x in valid if x["rating"] in CAUTIOUS]
    for g in (positive, neutral, cautious):
        g.sort(key=lambda z: z["score"] if z["score"] is not None else -1,
              reverse=True)

    # --- 维度极端值要点 ---
    bullets = []
    by_metric = lambda k: (lambda x: (x.get("metrics") or {}).get(k))
    by_signal = lambda k: (lambda x: (x.get("signals") or {}).get(k))
    b = _extreme(valid, by_metric("price_percentile"), "估值低位",
                 lambda v: f"分位 {v:.1f}%", high=False)
    if b:
        bullets.append(b)
    b = _extreme(valid, by_metric("price_percentile"), "估值高位",
                 lambda v: f"分位 {v:.1f}%", high=True)
    if b:
        bullets.append(b)
    b = _extreme(valid, by_signal("technical"), "技术分最高",
                 lambda v: f"{v:.1f} 分", high=True)
    if b:
        bullets.append(b)
    b = _extreme(valid, by_signal("crowding"), "最拥挤",
                 lambda v: f"拥挤度 {v:.1f}", high=True)
    if b:
        bullets.append(b)
    b = _extreme(valid, by_signal("risk"), "风险得分最高",
                 lambda v: f"{v:.1f} 分", high=True)
    if b:
        bullets.append(b)

    # --- 分类汇总 ---
    cat_sum = []
    for ck, label in (cfg.get("categories") or {}).items():
        group = [x for x in valid if x["category"] == ck]
        if not group:
            continue
        avg_c = round(sum(x["score"] for x in group) / len(group), 1)
        best = sorted(group, key=lambda z: z["score"], reverse=True)[0]
        cat_sum.append({
            "category": ck,
            "label": label,
            "count": len(group),
            "avg_score": avg_c,
            "best_name": best["name"],
            "best_score": best["score"],
        })

    return {
        "generated_at": now_iso,
        "summary": {
            "total": len(items),
            "valid": len(valid),
            "no_data": len(no_data),
            "avg_score": avg,
            "up": len(ups),
            "down": len(downs),
            "flat": len(flats),
            "by_rating": by_rating,
            "mood": _mood(avg),
        },
        "groups": {
            "positive": positive,
            "neutral": neutral,
            "cautious": cautious,
        },
        "bullets": bullets,
        "category_summary": cat_sum,
    }
