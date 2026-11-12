"""
主流程：读取 config.json -> 逐标的取数 -> 指标打分 -> 输出仪表台数据。

产物：
  data/dashboard.json        前端主数据（含近 120 日收盘序列）
  data/history/<code>.json   完整历史日线快照（供回溯/补算）

用法：
  python3 scripts/build.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sources
import analyze
import report

CN_TZ = timezone(timedelta(hours=8))
HISTORY_KEEP = 120  # dashboard 内联保留的最近交易日条数（前端展示窗口）


def _snapshot_path(code, subdir):
    return ROOT / "data" / subdir / f"{code}.json"


def _load_old_snapshot(code, subdir, key):
    """读取已有快照；文件缺失/损坏时返回空列表，不阻断构建。"""
    p = _snapshot_path(code, subdir)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            body = json.load(f)
        rows = body.get(key) if isinstance(body, dict) else body
        return rows if isinstance(rows, list) else []
    except (ValueError, OSError):
        return []


def _merge_by_date(old, new):
    """按日期合并两份升序序列：老数据保留，同日以本次拉取为准。"""
    by_date = {}
    for row in old or []:
        if row and row.get("date"):
            by_date[row["date"]] = row
    for row in new or []:
        if row and row.get("date"):
            by_date[row["date"]] = row
    return sorted(by_date.values(), key=lambda r: r["date"])


def _load_score_snapshot(code, model_version):
    """读取历史综合分快照 {date: score}；模型版本不符或损坏时返回空（强制回填）。"""
    p = _snapshot_path(code, "scores")
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            body = json.load(f)
        if body.get("model_version") != model_version:
            return {}
        return {r["date"]: r["score"] for r in body.get("scores", [])
                if r.get("date") and r.get("score") is not None}
    except (ValueError, OSError, AttributeError):
        return {}


def load_config():
    with open(ROOT / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_one(item, cfg):
    """返回 (snapshot, history, source_note)。"""
    is_cn = item["region"] == "CN"
    snapshot, history, note = None, None, []

    if item.get("kind") == "fund":
        history = sources.eastmoney_fund_history(
            item.get("eastmoney") or item["code"])
        if history:
            note.append("历史:天天基金(净值)")
    elif is_cn:
        snapshot = sources.hithink_snapshot(item, cfg)
        if snapshot is None:
            snapshot = sources.tencent_realtime(item.get("tencent"))
            if snapshot:
                note.append("实时:腾讯(免费源)")
        else:
            note.append("实时:同花顺")

        history = sources.hithink_history(item, cfg,
                                          cfg["history_window"]["trading_days"])
        if history:
            note.append("历史:同花顺")
        else:
            history = sources.sina_cn_history(item.get("sina_cn"),
                                              cfg["history_window"]["trading_days"])
            if history:
                note.append("历史:新浪(免费源)")
            else:
                history = sources.yahoo_history(item.get("yahoo"))
                if history:
                    note.append("历史:Yahoo(兜底源)")
    else:
        snapshot = sources.tencent_realtime(item.get("tencent"))
        if snapshot:
            note.append("实时:腾讯(免费源)")

        history = sources.sina_us_history(item.get("sina_us"))
        yh = sources.yahoo_history(item.get("yahoo"))
        if yh and (history is None or len(yh) > len(history)):
            history = yh
            note.append("历史:Yahoo(兜底源)")
        elif history:
            note.append("历史:新浪美股(免费源)")

    # 用历史末条补齐快照缺失字段
    if history:
        last = history[-1]
        if snapshot is None:
            snapshot = {"price": last["close"], "prev_close": None,
                        "change_pct": None, "open": last["open"],
                        "high": last["high"], "low": last["low"],
                        "ts": last["date"], "source": "history"}
        if snapshot.get("price") is None:
            snapshot["price"] = last["close"]
        if snapshot.get("change_pct") is None and len(history) >= 2:
            prev = history[-2]["close"]
            if prev:
                snapshot["change_pct"] = round(
                    (last["close"] - prev) / prev * 100, 2)
    return snapshot, history, "; ".join(note) if note else None


def main():
    cfg = load_config()
    items_out = []
    errors = []

    # 美债 10 年收益率（百分数），仅供 CAPE 标的 ERP 参考展示；取不到不阻断
    risk_free = sources.tnx_yield()

    for item in cfg["items"]:
        try:
            snapshot, fetched, note = fetch_one(item, cfg)
            # 历史快照按日期累积：保留已有老数据，并入本次拉取的新交易日，
            # 不剔除早期记录；同日以本次拉取为准。
            history = _merge_by_date(
                _load_old_snapshot(item["code"], "history", "history"), fetched)

            # CAPE 长历史序列（仅标普500 及其跟踪 ETF 配置），按日期累积合并。
            cape_series = None
            if item.get("cape_source") == "shiller_sp500":
                fetched_cape = sources.shiller_cape()
                cape_series = _merge_by_date(
                    _load_old_snapshot(item["code"], "cape", "cape"), fetched_cape)

            # 历史综合分快照：按日期累积；分位定档只取近窗口交易日（更早市场结构
            # 参考价值低，也避免长历史 O(n²) 回填）。缺失日期逐日滚动回填，模型版本
            # 变更时整体重建。
            model_version = cfg["meta"]["version"]
            window = cfg["history_window"]["trading_days"]
            old_scores = _load_score_snapshot(item["code"], model_version)
            recent = history[-window:] if len(history) > window else history
            start = len(history) - len(recent)
            score_series = []
            for i, hrow in enumerate(recent):
                d = hrow["date"]
                if d in old_scores:
                    sc = old_scores[d]
                else:
                    r = analyze.score_item(history[:start + i + 1], cfg,
                                           cape_series, risk_free)
                    sc = r["score"]
                if sc is not None:
                    score_series.append({"date": d, "score": round(sc, 1)})

            # 当日评级按“过去历史”分位定档（不含当日，无前视）。
            past_scores = [x["score"] for x in score_series[:-1]] if score_series else []
            result = analyze.score_item(history, cfg, cape_series, risk_free,
                                        past_scores=past_scores)

            record = {
                "code": item["code"],
                "name": item["name"],
                "category": item["category"],
                "region": item["region"],
                "kind": item["kind"],
                "price": (snapshot or {}).get("price"),
                "change_pct": (snapshot or {}).get("change_pct"),
                "open": (snapshot or {}).get("open"),
                "high": (snapshot or {}).get("high"),
                "low": (snapshot or {}).get("low"),
                "ts": (snapshot or {}).get("ts"),
                "data_source": note,
                "score": result["score"],
                "rating": result["rating"],
                "signals": result["signals"],
                "metrics": result["metrics"],
                "confidence": result["confidence"],
                "note": result["note"],
                "history": ([[h["date"], h["close"]] for h in history[-HISTORY_KEEP:]]
                            if history else []),
            }
            items_out.append(record)

            # 完整历史落盘
            if history:
                hist_dir = ROOT / "data" / "history"
                hist_dir.mkdir(parents=True, exist_ok=True)
                with open(hist_dir / f"{item['code']}.json", "w",
                          encoding="utf-8") as f:
                    json.dump({"code": item["code"], "name": item["name"],
                               "history": history}, f, ensure_ascii=False)

            # 历史综合分序列落盘（累积保留，供分位定档）
            if score_series:
                score_dir = ROOT / "data" / "scores"
                score_dir.mkdir(parents=True, exist_ok=True)
                with open(score_dir / f"{item['code']}.json", "w",
                          encoding="utf-8") as f:
                    json.dump({"code": item["code"], "name": item["name"],
                               "model_version": model_version,
                               "scores": score_series}, f, ensure_ascii=False)

            # CAPE 序列落盘（累积保留）
            if cape_series:
                cape_dir = ROOT / "data" / "cape"
                cape_dir.mkdir(parents=True, exist_ok=True)
                with open(cape_dir / f"{item['code']}.json", "w",
                          encoding="utf-8") as f:
                    json.dump({"code": item["code"], "name": item["name"],
                               "cape": cape_series}, f, ensure_ascii=False)
        except Exception as e:  # 单标的失败不阻断整体
            errors.append({"code": item["code"], "name": item["name"],
                           "error": str(e)})
            items_out.append({
                "code": item["code"], "name": item["name"],
                "category": item["category"], "region": item["region"],
                "kind": item["kind"], "price": None, "change_pct": None,
                "score": None, "rating": "取数失败", "signals": {},
                "metrics": {}, "confidence": "low", "note": str(e),
                "history": [],
            })

    now = datetime.now(CN_TZ)
    dashboard = {
        "meta": {
            "title": cfg["meta"]["title"],
            "description": cfg["meta"]["description"],
            "version": cfg["meta"]["version"],
            "generated_at": now.isoformat(timespec="seconds"),
            "weights": cfg["weights"],
            "thresholds": cfg["thresholds"],
            "categories": cfg["categories"],
        },
        "summary": {
            "total": len(items_out),
            "by_rating": _count_by(items_out, "rating"),
            "errors": errors,
        },
        "report": report.build_report(
            items_out, cfg, now.isoformat(timespec="seconds")),
        "items": items_out,
    }

    out_dir = ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "dashboard.json", "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    # 构建状态快照（供 Actions 通知步骤读取成功/失败及失败原因，不入库）
    status = {
        "ok": len(errors) == 0,
        "total": len(items_out),
        "failed": len(errors),
        "errors": [{"code": e["code"], "name": e["name"], "error": e["error"]}
                   for e in errors],
        "generated_at": dashboard["meta"]["generated_at"],
    }
    with open("/tmp/build-status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False)

    print(f"[build] 标的 {len(items_out)} 条，失败 {len(errors)} 条，"
          f"生成于 {dashboard['meta']['generated_at']}")
    for e in errors:
        print(f"  ! {e['code']} {e['name']}: {e['error']}")


def _count_by(items, key):
    agg = {}
    for it in items:
        k = it.get(key) or "未知"
        agg[k] = agg.get(k, 0) + 1
    return agg


if __name__ == "__main__":
    main()
