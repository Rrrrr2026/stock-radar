#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块情绪引擎 / Sector Sentiment (A股, 东财行业 + 概念板块)
=========================================================
复刻"板块情绪雷达"的核心分析 —— 用同花顺板块指数 + 资金流数据自研一套可解释的情绪指数:

  · 板块指数 + 情绪指数叠加走势 (情绪指数 0-100, 由 RSI/均线/量能/区间位置加权)
  · 板块资金流向综合评分排行 (正向/负向 TOP10 + 指标卡)
  · 板块热力块 (按涨跌幅着色)
  · 四象限分类 (资金净流 × 情绪强弱)

情绪指数算法为本项目自研(视频作者算法未公开), 数值不等同于其原版, 但形态与用途一致。
输出 docs/sector.js -> window.__SECTORS__ = {...}。
"""
from __future__ import annotations
import os
import sys
import json
import logging
import datetime as dt

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

N_CONCEPT_HIST = 44     # 概念太多(374), 只给资金净流最活跃的前 N 只算情绪叠加历史
HIST_DAYS = 400         # 约 1.5 年日线, 前端可选近1年


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
    # 行业广度(上涨/下跌家数) 来自 summary_ths
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
            "index_val": _r(r.get("行业指数"), 2),
            "pct": _r(r.get("行业-涨跌幅"), 2),
            "inflow": _r(r.get("流入资金"), 2), "outflow": _r(r.get("流出资金"), 2),
            "net": _r(r.get("净额"), 2),                     # 亿元
            "companies": int(_num(r.get("公司家数")) or 0),
            "up": int(up) if up is not None else None,
            "down": int(down) if down is not None else None,
            "lead": str(r.get("领涨股") or "").strip() or None,
            "lead_pct": _r(r.get("领涨股-涨跌幅"), 2),
        })
    return out


# --------------------------------------------------------------------------
#  情绪指数 (自研, 0-100) —— 由板块指数日线派生
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

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(50)

    ma20 = close.rolling(20, min_periods=5).mean()
    ma_score = 50 + ((close / ma20 - 1) * 500).clip(-50, 50)      # 站上/跌破 20 日线
    lo = close.rolling(60, min_periods=10).min()
    hi = close.rolling(60, min_periods=10).max()
    pos = ((close - lo) / (hi - lo).replace(0, np.nan) * 100).clip(0, 100).fillna(50)  # 60日区间位置
    vma = vol.rolling(20, min_periods=5).mean()
    vol_score = (50 + (vol / vma - 1) * 25).clip(0, 100).fillna(50)  # 量能

    senti = (0.35 * rsi + 0.25 * pos + 0.25 * ma_score + 0.15 * vol_score)
    senti = senti.ewm(span=3, adjust=False).mean().clip(0, 100)

    d = d.assign(_c=close, _s=senti).dropna(subset=["_c"]).tail(260)
    return {
        "dates": list(d["date"]),
        "close": [round(float(x), 2) for x in d["_c"]],
        "sentiment": [round(float(x), 1) for x in d["_s"]],
        "now": round(float(d["_s"].iloc[-1]), 1) if len(d) else None,
    }


def _hist(kind: str, name: str) -> dict | None:
    ak = ds._ak()
    fn = ak.stock_board_industry_index_ths if kind == "industry" else ak.stock_board_concept_index_ths
    end = dt.date.today().strftime("%Y%m%d")
    start = (dt.date.today() - dt.timedelta(days=HIST_DAYS)).strftime("%Y%m%d")
    df = _safe(fn, symbol=name, start_date=start, end_date=end)
    return _sentiment(df)


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

    nets = [b.get("net") for b in boards]
    pcts = [b.get("pct") for b in boards]
    sentis = [b.get("sentiment_now") for b in boards]
    breadths = [((b["up"] / (b["up"] + b["down"]) * 100) if (b.get("up") is not None and b.get("down")
                 is not None and (b["up"] + b["down"]) > 0) else None) for b in boards]
    zn, zp, zs = z(nets), z(pcts), z(sentis)
    zb = z(breadths)
    for i, b in enumerate(boards):
        raw = 0.4 * zn[i] + 0.25 * zp[i] + 0.25 * zs[i] + 0.1 * zb[i]
        b["score"] = round(50 + raw * 15, 1)                     # ~0-100
        b["breadth"] = round(breadths[i], 1) if breadths[i] is not None else None

    ranked = sorted(boards, key=lambda b: (b.get("score") is not None, b.get("score") or -1), reverse=True)
    keys = ("name", "kind", "score", "net", "pct", "sentiment_now", "breadth", "companies", "lead", "index_val")
    slim = lambda b: {k: b.get(k) for k in keys}
    pos_top = [slim(b) for b in ranked[:10]]
    neg_top = [slim(b) for b in ranked[::-1][:10]]
    net_ok = [b for b in boards if b.get("net") is not None]
    pos_cnt = sum(1 for b in net_ok if b["net"] > 0)
    neg_cnt = sum(1 for b in net_ok if b["net"] < 0)
    total_net = round(sum(b["net"] for b in net_ok), 1)
    strongest = max(net_ok, key=lambda b: b["net"])["name"] if net_ok else None
    weakest = min(net_ok, key=lambda b: b["net"])["name"] if net_ok else None
    return {"pos_top10": pos_top, "neg_top10": neg_top,
            "cards": {"pos_cnt": pos_cnt, "neg_cnt": neg_cnt, "total_net": total_net,
                      "strongest": strongest, "weakest": weakest, "n": len(boards)}}


# --------------------------------------------------------------------------
#  组装
# --------------------------------------------------------------------------
def build():
    log.info("拉取行业 / 概念板块快照 ...")
    industry = _boards("industry")
    concept = _boards("concept")
    log.info("行业 %d 只, 概念 %d 只", len(industry), len(concept))

    # 情绪叠加历史: 全部行业 + 资金净流最活跃的前 N 概念 (单线程, THS)
    conc_sorted = sorted([c for c in concept if c.get("net") is not None],
                         key=lambda c: abs(c["net"]), reverse=True)
    hist_targets = [("industry", b) for b in industry] + \
                   [("concept", b) for b in conc_sorted[:N_CONCEPT_HIST]]
    log.info("计算 %d 只板块的情绪叠加历史 (单线程) ...", len(hist_targets))
    for i, (kind, b) in enumerate(hist_targets, 1):
        h = _hist(kind, b["name"])
        if h:
            b["hist"] = h
            b["sentiment_now"] = h.get("now")
        if i % 10 == 0:
            log.info("  %d/%d", i, len(hist_targets))

    rank_ind = _score_and_rank(industry)
    rank_con = _score_and_rank(concept)

    # 四象限散点 (资金净流 × 情绪强弱) —— 有情绪值的板块
    def quad(boards):
        return [{"name": b["name"], "kind": b["kind"], "net": b["net"],
                 "sentiment": b.get("sentiment_now"), "pct": b["pct"], "companies": b["companies"]}
                for b in boards if b.get("sentiment_now") is not None and b.get("net") is not None]

    trade_date = None
    for b in industry + concept:
        if b.get("hist") and b["hist"].get("dates"):
            trade_date = b["hist"]["dates"][-1]; break

    payload = {
        "meta": {"updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "trade_date": trade_date, "n_industry": len(industry), "n_concept": len(concept),
                 "note": "情绪指数为本项目自研(RSI/均线/量能/区间位置加权, 0-100), 非视频原算法; 仅供研究。"},
        "industry": industry, "concept": concept,
        "ranking": {"industry": rank_ind, "concept": rank_con},
        "quadrant": {"industry": quad(industry), "concept": quad(concept)},
    }
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, "sector.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.__SECTORS__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    log.info("已写出 %s (行业 %d / 概念 %d, 含历史 %d)", out, len(industry), len(concept),
             sum(1 for b in industry + concept if b.get("hist")))
    return out


if __name__ == "__main__":
    build()
