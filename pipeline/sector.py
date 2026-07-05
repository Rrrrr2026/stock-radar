#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块情绪引擎 / Sector Sentiment (A股, 东财行业 + 概念板块)
=========================================================
复刻"板块情绪雷达"并加入分析解读:

  · 板块指数 + 情绪指数叠加走势 (情绪指数 0-100, 自研: RSI/均线/量能/区间位置加权)
  · 板块资金流向综合评分排行 (正向/负向 TOP10 + 指标卡) + 四象限分类
  · 全市场情绪演变走势 (各板块情绪日度均值, 立刻可看过去一年)
  · 每日快照记录 (每次运行追加一条, 历史逐日累积 -> sector_history.js)
  · 分析解读 (规则引擎): 每个板块 + 全市场 —— 指数vs情绪背离/高位/机会/风险, 中英双语
  · 板块 -> 成分股联动: 每板块 top10 成分 (需可访问东财 push2; 否则回退到领涨股)

情绪指数与解读均为本项目自研(视频作者算法未公开), 仅供研究, 不构成投资建议。
输出 docs/sector.js -> window.__SECTORS__, docs/sector_history.js -> window.__SECTOR_HISTORY__。
"""
from __future__ import annotations
import os
import sys
import json
import logging
import datetime as dt
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
import pandas as pd

from . import datasource as ds

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("radar.sector")

_HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(_HERE, "..", "docs")

N_CONCEPT_HIST = 44
HIST_DAYS = 400
N_CONS = 10          # 每板块保留 top N 成分股
HISTORY_KEEP = 180   # 每日快照保留天数


def _num(x):
    try:
        v = float(x)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return None


def _r(v, nd=2):
    v = _num(v)
    return None if v is None else round(v, nd)


def _safe(fn, *a, **k):
    try:
        return ds.call_with_retry(fn, *a, **k)
    except Exception as e:
        log.debug("sector fetch failed: %s", e)
        return None


# --------------------------------------------------------------------------
#  当前板块快照 (资金流 + 广度)
# --------------------------------------------------------------------------
def _boards(kind: str) -> list[dict]:
    ak = ds._ak()
    fn = ak.stock_fund_flow_industry if kind == "industry" else ak.stock_fund_flow_concept
    df = _safe(fn, symbol="即时")
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    breadth = {}
    if kind == "industry":
        sm = _safe(ak.stock_board_industry_summary_ths)
        if isinstance(sm, pd.DataFrame) and not sm.empty and "板块" in sm.columns:
            for _, r in sm.iterrows():
                breadth[str(r["板块"])] = (_num(r.get("上涨家数")), _num(r.get("下跌家数")))
    out = []
    for _, r in df.iterrows():
        name = str(r["行业"]).strip()
        up, down = breadth.get(name, (None, None))
        out.append({
            "name": name, "kind": kind,
            "index_val": _r(r.get("行业指数"), 2), "pct": _r(r.get("行业-涨跌幅"), 2),
            "inflow": _r(r.get("流入资金"), 2), "outflow": _r(r.get("流出资金"), 2),
            "net": _r(r.get("净额"), 2),
            "companies": int(_num(r.get("公司家数")) or 0),
            "up": int(up) if up is not None else None,
            "down": int(down) if down is not None else None,
            "lead": str(r.get("领涨股") or "").strip() or None,
            "lead_pct": _r(r.get("领涨股-涨跌幅"), 2),
        })
    return out


# --------------------------------------------------------------------------
#  情绪指数 (自研, 0-100)
# --------------------------------------------------------------------------
def _sentiment(df: pd.DataFrame) -> dict | None:
    if not isinstance(df, pd.DataFrame) or df.empty or "收盘价" not in df.columns:
        return None
    d = df.copy()
    d["date"] = d["日期"].astype(str)
    close = pd.to_numeric(d["收盘价"], errors="coerce")
    vol = pd.to_numeric(d.get("成交量"), errors="coerce")
    if close.notna().sum() < 40:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50)
    ma20 = close.rolling(20, min_periods=5).mean()
    ma_score = 50 + ((close / ma20 - 1) * 500).clip(-50, 50)
    lo = close.rolling(60, min_periods=10).min()
    hi = close.rolling(60, min_periods=10).max()
    pos = ((close - lo) / (hi - lo).replace(0, np.nan) * 100).clip(0, 100).fillna(50)
    vma = vol.rolling(20, min_periods=5).mean()
    vol_score = (50 + (vol / vma - 1) * 25).clip(0, 100).fillna(50)
    senti = (0.35 * rsi + 0.25 * pos + 0.25 * ma_score + 0.15 * vol_score).ewm(span=3, adjust=False).mean().clip(0, 100)
    d = d.assign(_c=close, _s=senti).dropna(subset=["_c"]).tail(260)
    return {"dates": list(d["date"]),
            "close": [round(float(x), 2) for x in d["_c"]],
            "sentiment": [round(float(x), 1) for x in d["_s"]],
            "now": round(float(d["_s"].iloc[-1]), 1) if len(d) else None}


def _hist(kind: str, name: str) -> dict | None:
    ak = ds._ak()
    fn = ak.stock_board_industry_index_ths if kind == "industry" else ak.stock_board_concept_index_ths
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=HIST_DAYS)).strftime("%Y%m%d")
    return _sentiment(_safe(fn, symbol=name, start_date=start, end_date=end))


# --------------------------------------------------------------------------
#  分析解读 (规则引擎) —— 每个板块
# --------------------------------------------------------------------------
def _board_insights(b: dict) -> None:
    h = b.get("hist")
    if not h or not h.get("close"):
        return
    close = h["close"]; sen = h["sentiment"]; n = len(close)
    if n < 25:
        return
    k = min(21, n - 1)
    idx20 = (close[-1] / close[-1 - k] - 1) * 100 if close[-1 - k] else None
    sen_now = sen[-1]; sen20 = sen[-1] - sen[-1 - k]
    win = close[-min(250, n):]
    lo, hi = min(win), max(win)
    pos = (close[-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0
    net = b.get("net"); ins = []

    def add(level, zh, en):
        ins.append({"level": level, "text": zh, "text_en": en})

    if idx20 is not None:
        if idx20 > 3 and sen20 < -4:
            add("warn", f"指数近月涨{idx20:.0f}%但情绪回落{sen20:.0f} —— 价升情绪降的顶背离, 上涨动能衰减, 高位防回调",
                f"Index +{idx20:.0f}% over the month while sentiment fell {sen20:.0f} — a bearish (price-up, sentiment-down) divergence; momentum is fading, watch for a pullback")
        elif idx20 < -3 and sen20 > 4:
            add("good", f"指数近月跌{idx20:.0f}%但情绪回升{sen20:.0f} —— 价跌情绪升的底背离, 或临近阶段底, 可左侧关注",
                f"Index {idx20:.0f}% over the month while sentiment rose +{sen20:.0f} — a bullish (price-down, sentiment-up) divergence; possibly near a bottom, worth watching")
        elif idx20 > 2 and sen20 >= 0:
            add("good", "指数与情绪同步向上 —— 量价情绪共振, 趋势健康(但注意不追高)",
                "Index and sentiment rising together — healthy resonance; don't chase the top")
        elif idx20 < -2 and sen20 < 0:
            add("info", "指数与情绪同步向下 —— 弱势下行, 暂观望",
                "Index and sentiment falling together — weak trend, stay on the sidelines")

    if sen_now >= 75:
        add("warn", f"情绪指数{sen_now:.0f}处于过热区(≥75) —— 短期亢奋, 追高风险大, 关注止盈",
            f"Sentiment {sen_now:.0f} is overheated (≥75) — euphoric short-term; chasing is risky, mind profit-taking")
    elif sen_now <= 30:
        add("info", f"情绪指数{sen_now:.0f}处于低迷区(≤30) —— 或超跌, 具备左侧价值(需右侧确认)",
            f"Sentiment {sen_now:.0f} is depressed (≤30) — possibly oversold; left-side value but needs confirmation")

    if pos >= 85:
        add("warn", f"板块指数处于近一年{pos:.0f}%高位 —— 估值/情绪透支风险",
            f"Index is at the {pos:.0f}% high of its 1-year range — valuation/sentiment may be stretched")
    elif pos <= 18:
        add("info", f"板块指数处于近一年{pos:.0f}%低位区",
            f"Index is at the {pos:.0f}% low of its 1-year range")

    if net is not None:
        if net > 0 and sen_now >= 70 and pos >= 75:
            add("warn", f"高位仍有资金净流入{net:.0f}亿 —— 高位接力, 谨慎追高, 一旦转为流出易反转",
                f"Still +{net:.0f}00M CNY net inflow at highs — late-stage chase; reversal risk once flows turn")
        elif net < 0 and pos >= 75:
            add("warn", f"高位资金净流出{net:.0f}亿 —— 派发迹象, 注意回调",
                f"Net outflow {net:.0f}00M CNY at highs — distribution signal, watch for a pullback")
        elif net > 0 and sen_now <= 45:
            add("good", f"低位/温和情绪下资金净流入{net:.0f}亿 —— 吸筹迹象, 潜在机会",
                f"Net inflow +{net:.0f}00M CNY at low/mild sentiment — accumulation signal, potential opportunity")

    is_high = sen_now >= 72 or pos >= 82
    is_opp = (sen_now <= 45 and (net or 0) > 0) or (idx20 is not None and idx20 < -3 and sen20 > 4)
    if is_opp and not is_high:
        verdict, ven, vlv = "偏机会 · 低位吸筹/底背离", "Opportunity-leaning · accumulation / bullish divergence", "good"
    elif is_high and ((net or 0) < 0 or sen20 < -4):
        verdict, ven, vlv = "偏风险 · 高位/派发/顶背离", "Risk-leaning · high / distribution / bearish divergence", "warn"
    elif idx20 is not None and idx20 > 2 and sen20 >= 0 and (net or 0) > 0:
        verdict, ven, vlv = "强势顺势 · 共振向上(防追高)", "Strong trend · upward resonance (don't chase)", "good"
    else:
        verdict, ven, vlv = "中性观望", "Neutral · wait and see", "info"

    b["insights"] = ins
    b["verdict"] = verdict; b["verdict_en"] = ven; b["verdict_level"] = vlv
    b["pos"] = round(pos, 0); b["idx20"] = _r(idx20, 1); b["sen20"] = _r(sen20, 1)


# --------------------------------------------------------------------------
#  分析解读 —— 全市场
# --------------------------------------------------------------------------
def _market_insights(industry: list, concept: list) -> dict:
    def avg_sen(bs):
        v = [b["sentiment_now"] for b in bs if b.get("sentiment_now") is not None]
        return round(sum(v) / len(v), 1) if v else None
    ind_now, con_now = avg_sen(industry), avg_sen(concept)
    allb = [b for b in industry + concept if b.get("sentiment_now") is not None and b.get("net") is not None]
    net_ok = [b for b in industry + concept if b.get("net") is not None]
    pos_cnt = sum(1 for b in net_ok if b["net"] > 0)
    neg_cnt = sum(1 for b in net_ok if b["net"] < 0)
    total_net = round(sum(b["net"] for b in net_ok), 1)
    overheated = sorted([b for b in allb if b["sentiment_now"] >= 72 and b["net"] > 0],
                        key=lambda b: -b["net"])[:4]
    opp = sorted([b for b in allb if b["sentiment_now"] <= 42 and b["net"] > 0],
                 key=lambda b: -b["net"])[:4]
    leaders = sorted(net_ok, key=lambda b: -b["net"])[:4]
    avg = round(((ind_now or 50) + (con_now or 50)) / 2, 1)
    state = "偏热" if avg >= 60 else ("偏冷" if avg <= 40 else "中性")
    state_en = "hot" if avg >= 60 else ("cold" if avg <= 40 else "neutral")
    nm = lambda L: "、".join(b["name"] for b in L) or "无"
    nme = lambda L: ", ".join(b["name"] for b in L) or "none"
    ins = []

    def add(level, zh, en):
        ins.append({"level": level, "text": zh, "text_en": en})

    add("info", f"全市场板块情绪均值 {avg}（{state}）—— 行业均值 {ind_now}, 概念均值 {con_now}。",
        f"Market-wide board sentiment {avg} ({state_en}) — industry avg {ind_now}, concept avg {con_now}.")
    fund_state = "偏多" if total_net > 0 else ("偏空" if total_net < 0 else "均衡")
    fund_en = "risk-on" if total_net > 0 else ("risk-off" if total_net < 0 else "balanced")
    add("good" if total_net > 0 else "warn" if total_net < 0 else "info",
        f"资金面：净流入板块 {pos_cnt} 个 vs 净流出 {neg_cnt} 个, 合计净 {total_net} 亿（{fund_state}）。",
        f"Flows: {pos_cnt} boards net-in vs {neg_cnt} net-out, total net {total_net}00M CNY ({fund_en}).")
    if overheated:
        add("warn", f"高位需警惕：{nm(overheated)} —— 情绪过热(≥72)且资金仍在博弈, 防高位回调。",
            f"Watch the highs: {nme(overheated)} — overheated (≥72) with money still fighting; pullback risk.")
    if opp:
        add("good", f"低位关注：{nm(opp)} —— 情绪偏低(≤42)却获资金净流入, 或早期吸筹。",
            f"Low-and-accumulating: {nme(opp)} — low sentiment (≤42) yet net inflow; possible early accumulation.")
    if leaders:
        add("info", f"资金主线（最强吸金）：{nm(leaders)}。",
            f"Money mainlines (strongest inflow): {nme(leaders)}.")
    summary = (f"当前板块情绪{state}(均值{avg})、资金面{fund_state}(净{total_net}亿)。"
               f"高位板块{nm(overheated) if overheated else '暂无明显过热'}需防回调; "
               f"低位吸筹关注{nm(opp) if opp else '暂无'}。仅供研究, 不构成投资建议。")
    summary_en = (f"Board sentiment is {state_en} (avg {avg}); flows are {fund_en} (net {total_net}00M). "
                  f"Watch highs: {nme(overheated) if overheated else 'none obvious'}; "
                  f"low-accumulation: {nme(opp) if opp else 'none'}. Research only, not advice.")
    return {"avg": avg, "state": state, "state_en": state_en,
            "ind_now": ind_now, "con_now": con_now,
            "pos_cnt": pos_cnt, "neg_cnt": neg_cnt, "total_net": total_net,
            "insights": ins, "summary": summary, "summary_en": summary_en}


# --------------------------------------------------------------------------
#  情绪演变 (各板块情绪日度均值)
# --------------------------------------------------------------------------
def _trend(boards: list) -> tuple[list, list]:
    acc = defaultdict(list)
    for b in boards:
        h = b.get("hist")
        if not h:
            continue
        for d, s in zip(h["dates"], h["sentiment"]):
            if s is not None:
                acc[d].append(s)
    dates = sorted(acc.keys())[-250:]
    return dates, [round(sum(acc[d]) / len(acc[d]), 1) for d in dates]


# --------------------------------------------------------------------------
#  综合评分 + 排行
# --------------------------------------------------------------------------
def _score_and_rank(boards: list[dict]) -> dict:
    if not boards:
        return {}

    def z(vals):
        a = np.array([v if v is not None else np.nan for v in vals], dtype=float)
        m, s = np.nanmean(a), np.nanstd(a)
        if not s or np.isnan(s):
            return [0.0] * len(a)
        return [(0.0 if np.isnan(x) else (x - m) / s) for x in a]

    breadths = [((b["up"] / (b["up"] + b["down"]) * 100) if (b.get("up") is not None and b.get("down")
                 is not None and (b["up"] + b["down"]) > 0) else None) for b in boards]
    zn = z([b.get("net") for b in boards]); zp = z([b.get("pct") for b in boards])
    zs = z([b.get("sentiment_now") for b in boards]); zb = z(breadths)
    for i, b in enumerate(boards):
        b["score"] = round(50 + (0.4 * zn[i] + 0.25 * zp[i] + 0.25 * zs[i] + 0.1 * zb[i]) * 15, 1)
        b["breadth"] = round(breadths[i], 1) if breadths[i] is not None else None
    ranked = sorted(boards, key=lambda b: (b.get("score") is not None, b.get("score") or -1), reverse=True)
    keys = ("name", "kind", "score", "net", "pct", "sentiment_now", "breadth", "companies", "lead",
            "index_val", "verdict", "verdict_en", "verdict_level")
    slim = lambda b: {k: b.get(k) for k in keys}
    net_ok = [b for b in boards if b.get("net") is not None]
    return {"pos_top10": [slim(b) for b in ranked[:10]],
            "neg_top10": [slim(b) for b in ranked[::-1][:10]],
            "cards": {"pos_cnt": sum(1 for b in net_ok if b["net"] > 0),
                      "neg_cnt": sum(1 for b in net_ok if b["net"] < 0),
                      "total_net": round(sum(b["net"] for b in net_ok), 1),
                      "strongest": max(net_ok, key=lambda b: b["net"])["name"] if net_ok else None,
                      "weakest": min(net_ok, key=lambda b: b["net"])["name"] if net_ok else None,
                      "n": len(boards)}}


# --------------------------------------------------------------------------
#  成分股 (需可访问东财 push2; 一次探测, 不通则整轮跳过)
# --------------------------------------------------------------------------
def _cons_probe() -> bool:
    ak = ds._ak()
    try:
        df = ds.call_with_retry(ak.stock_board_industry_cons_em, symbol="半导体")
        return isinstance(df, pd.DataFrame) and not df.empty
    except Exception:
        return False


def _cons(kind: str, name: str) -> list:
    ak = ds._ak()
    fn = ak.stock_board_industry_cons_em if kind == "industry" else ak.stock_board_concept_cons_em
    df = _safe(fn, symbol=name)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    ccol = ds.pick_col(df, ["代码"], contains=True)
    ncol = ds.pick_col(df, ["名称"], contains=True)
    pcol = ds.pick_col(df, ["涨跌幅"], contains=True)
    prcol = ds.pick_col(df, ["最新价"], contains=True)
    if not ccol or not ncol:
        return []
    df = df.copy()
    if pcol:
        df = df.sort_values(pcol, ascending=False)
    out = []
    for _, r in df.head(N_CONS).iterrows():
        out.append({"code": str(r[ccol]).zfill(6), "name": str(r[ncol]).strip(),
                    "pct": _r(r[pcol], 2) if pcol else None,
                    "price": _r(r[prcol], 2) if prcol else None})
    return out


# --------------------------------------------------------------------------
#  每日快照 (逐日累积)
# --------------------------------------------------------------------------
def _load_history() -> list:
    p = os.path.join(DOCS, "sector_history.js")
    if not os.path.exists(p):
        return []
    try:
        t = open(p, encoding="utf-8").read()
        return json.loads(t[t.index("=") + 1:].rstrip().rstrip(";"))
    except Exception:
        return []


def _append_snapshot(trade_date, market, rank_ind, rank_con):
    date = trade_date or dt.date.today().isoformat()
    top = lambda rk: [{"name": b["name"], "score": b["score"], "net": b["net"]}
                      for b in (rk.get("pos_top10") or [])[:5]]
    snap = {"date": date, "avg": market["avg"], "state": market["state"],
            "ind_now": market["ind_now"], "con_now": market["con_now"],
            "total_net": market["total_net"], "pos_cnt": market["pos_cnt"], "neg_cnt": market["neg_cnt"],
            "ind_top": top(rank_ind), "con_top": top(rank_con)}
    hist = [e for e in _load_history() if e.get("date") != date] + [snap]
    hist = sorted(hist, key=lambda e: e["date"])[-HISTORY_KEEP:]
    with open(os.path.join(DOCS, "sector_history.js"), "w", encoding="utf-8") as f:
        f.write("window.__SECTOR_HISTORY__ = " + json.dumps(hist, ensure_ascii=False) + ";\n")
    log.info("每日快照: 累计 %d 天 (最新 %s)", len(hist), date)


# --------------------------------------------------------------------------
#  组装
# --------------------------------------------------------------------------
def build():
    log.info("拉取行业 / 概念板块快照 ...")
    industry = _boards("industry")
    concept = _boards("concept")
    log.info("行业 %d 只, 概念 %d 只", len(industry), len(concept))

    conc_sorted = sorted([c for c in concept if c.get("net") is not None], key=lambda c: abs(c["net"]), reverse=True)
    hist_targets = [("industry", b) for b in industry] + [("concept", b) for b in conc_sorted[:N_CONCEPT_HIST]]
    log.info("计算 %d 只板块的情绪叠加历史 + 解读 (单线程) ...", len(hist_targets))
    for i, (kind, b) in enumerate(hist_targets, 1):
        h = _hist(kind, b["name"])
        if h:
            b["hist"] = h
            b["sentiment_now"] = h.get("now")
            _board_insights(b)
        if i % 15 == 0:
            log.info("  %d/%d", i, len(hist_targets))

    # 成分股 (东财可达时)
    cons_ok = _cons_probe()
    if cons_ok:
        log.info("东财成分股可用, 拉取每板块 top%d 成分 ...", N_CONS)
        for kind, b in hist_targets:
            c = _cons(kind, b["name"])
            if c:
                b["cons"] = c
    else:
        log.warning("东财 push2 成分股不可达(本环境常见), 跳过成分股; 前端回退到领涨股。"
                    "在可访问东财的机器上重跑本管道即可填充。")

    rank_ind = _score_and_rank(industry)
    rank_con = _score_and_rank(concept)
    market = _market_insights(industry, concept)

    ind_dates, ind_avg = _trend(industry)
    con_dates, con_avg = _trend(concept)
    # 对齐到并集日期
    all_dates = sorted(set(ind_dates) | set(con_dates))[-250:]
    im = dict(zip(ind_dates, ind_avg)); cm = dict(zip(con_dates, con_avg))
    trend = {"dates": all_dates,
             "industry_avg": [im.get(d) for d in all_dates],
             "concept_avg": [cm.get(d) for d in all_dates]}

    def quad(boards):
        return [{"name": b["name"], "kind": b["kind"], "net": b["net"], "sentiment": b.get("sentiment_now"),
                 "pct": b["pct"], "companies": b["companies"], "verdict": b.get("verdict")}
                for b in boards if b.get("sentiment_now") is not None and b.get("net") is not None]

    trade_date = all_dates[-1] if all_dates else None
    payload = {
        "meta": {"updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "trade_date": trade_date,
                 "n_industry": len(industry), "n_concept": len(concept), "cons": cons_ok,
                 "note": "情绪指数与分析解读均为本项目自研(RSI/均线/量能/区间位置加权, 0-100), 非视频原算法; 仅供研究, 不构成投资建议。"},
        "industry": industry, "concept": concept,
        "ranking": {"industry": rank_ind, "concept": rank_con},
        "quadrant": {"industry": quad(industry), "concept": quad(concept)},
        "trend": trend, "market": market,
    }
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "sector.js"), "w", encoding="utf-8") as f:
        f.write("window.__SECTORS__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    log.info("已写出 sector.js (行业 %d / 概念 %d, 含历史 %d, 成分股=%s)",
             len(industry), len(concept), sum(1 for b in industry + concept if b.get("hist")), cons_ok)

    _append_snapshot(trade_date, market, rank_ind, rank_con)
    return os.path.join(DOCS, "sector.js")


if __name__ == "__main__":
    build()
