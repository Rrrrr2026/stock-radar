#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股个股深度档案 / Company Profile (US)
=======================================
与 A 股档案对齐的同一套"公司分析"栏目, 但用美股真实数据源:
  1. 公司简介 (longBusinessSummary) + 管理层 (companyOfficers, 含薪酬)
  2. 营收流向拆解 (每 1 美元营收去了哪: 成本/研发/销管/税/净利, 增速异常标注) —— 饼图
  3. 营收增速 (年度同比 + 近 8 季度)
  4. 现金流 (经营/自由/资本开支/收购/回购/分红) + 自动"漏洞"要点
  5. 风险 (ISS 治理评分 + 做空/偿债)
  6. 政策 —— 前端按行业静态梳理
  7. 利好vs利空新闻 + 期权博弈 (真实期权: P/C 持仓比 + 最大痛点) —— 非代理
  8. 暗池 (FINRA 场外空头成交占比) —— 真实代理指标
单位: 百万美元($M)。缺失安全: 取不到 -> None/[]。
"""
from __future__ import annotations
import logging

import numpy as np
import pandas as pd

from . import datasource_us as ds

log = logging.getLogger("radar.profile_us")
M = 1e6

_POS = ["beat", "surge", "soar", "record", "upgrade", "raise", "growth", "strong",
        "wins", "win ", "award", "approval", "launch", "buyback", "dividend", "profit",
        "rally", "jump", "outperform", "high", "expand", "partnership", "breakthrough"]
_NEG = ["miss", "fall", "plunge", "drop", "downgrade", "cut", "lawsuit", "probe",
        "recall", "layoff", "loss", "decline", "warn", "weak", "slump", "sink",
        "delay", "investigation", "fraud", "bankrupt", "sell-off", "selloff", "slash"]


def _num(x):
    if x is None:
        return None
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
        log.debug("us fetch failed: %s", e)
        return None


def _row(df, names):
    """从 yfinance 报表 (index=科目, columns=日期倒序) 取一行, 返回 (dates_asc, values_asc[$M])。"""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None, None
    for nm in names:
        if nm in df.index:
            s = df.loc[nm]
            cols = list(df.columns)[::-1]
            vals = [(_num(s[c]) / M if _num(s[c]) is not None else None) for c in cols]
            dates = [str(c)[:4] for c in cols]
            return dates, vals
    return None, None


def _officers(info, top=6):
    out = []
    for o in (info.get("companyOfficers") or [])[:top]:
        if not isinstance(o, dict) or not o.get("name"):
            continue
        pay = o.get("totalPay")
        if isinstance(pay, dict):
            pay = pay.get("raw")
        pay = _num(pay)
        out.append({"name": o.get("name"), "title": o.get("title") or "—",
                    "age": o.get("age"), "pay_m": round(pay / M, 2) if pay else None})
    return out


def _segments(fin, revenue_dates, revenue_vals):
    """营收流向拆解: 每 1 美元营收 -> 成本/研发/销管/税/净利。带同比。"""
    if not revenue_vals:
        return None
    rev, ri = None, None
    for i in range(len(revenue_vals) - 1, -1, -1):
        if revenue_vals[i]:
            rev, ri = revenue_vals[i], i
            break
    if not rev or rev <= 0:
        return None
    prev = revenue_vals[ri - 1] if ri > 0 else None

    def latest(names):
        d, v = _row(fin, names)
        if not v:
            return None, None
        return (v[ri] if ri < len(v) else None), (v[ri - 1] if (ri > 0 and ri - 1 < len(v)) else None)

    specs = [
        ("Cost of Revenue", ["Cost Of Revenue", "Reconciled Cost Of Revenue"]),
        ("R&D", ["Research And Development"]),
        ("SG&A", ["Selling General And Administration", "Selling General And Administrative"]),
        ("Income Tax", ["Tax Provision", "Income Tax Expense"]),
        ("Net Income", ["Net Income", "Net Income Common Stockholders"]),
    ]
    items = []
    for label, names in specs:
        cur, pv = latest(names)
        if cur is None:
            continue
        yoy = _r((cur / pv - 1) * 100, 1) if (pv and pv > 0 and cur is not None) else None
        items.append({"name": label, "value": _r(cur, 0), "pct": _r(cur / rev * 100, 1),
                      "margin": None, "yoy": yoy})
    if not items:
        return None
    return {"date": revenue_dates[ri] if revenue_dates else "", "by": "营收流向拆解",
            "total": _r(rev, 0), "items": items}


def _cash_insights(cf):
    notes = []
    if not cf or not cf.get("years"):
        return notes
    yrs = cf["years"]

    def v(k, i):
        a = cf.get(k) or []
        return a[i] if (i is not None and i < len(a)) else None

    ocf, i = None, None
    arr = cf.get("ocf") or []
    for j in range(len(arr) - 1, -1, -1):
        if arr[j] is not None:
            ocf, i = arr[j], j
            break
    if i is None:
        return notes
    yr = yrs[i]
    ni = v("net_income", i); fcf = v("fcf", i); capex = v("capex", i)
    acq = v("acq", i); buyback = v("buyback", i); dividend = v("dividend", i)
    diss = v("debt_iss", i); drep = v("debt_rep", i)
    debt_net = ((diss or 0) + (drep or 0)) if (diss is not None or drep is not None) else None

    def add(lvl, zh, en):
        notes.append({"level": lvl, "text": zh, "text_en": en})

    if ocf is not None and ni is not None and ni > 0:
        r = ocf / ni
        if r < 0.7:
            add("warn", f"FY{yr} 经营现金流仅为净利润的{r*100:.0f}% —— 利润未充分转化为现金, 盈利质量需警惕",
                f"FY{yr} operating cash flow is only {r*100:.0f}% of net income — earnings quality concern")
        elif r > 1.1:
            add("good", f"FY{yr} 经营现金流为净利润的{r*100:.0f}% —— 利润含金量高",
                f"FY{yr} operating cash flow is {r*100:.0f}% of net income — high-quality earnings")
    if fcf is not None and fcf < 0:
        add("warn", f"FY{yr} 自由现金流为负(${fcf:,.0f}M) —— 经营造血不足以覆盖资本开支",
            f"FY{yr} free cash flow is negative (${fcf:,.0f}M) — operations don't cover capex")
    if acq is not None and acq < 0 and ocf and ocf > 0 and abs(acq) >= 0.3 * ocf:
        add("warn", f"FY{yr} 收购支出${abs(acq):,.0f}M, 约为经营现金流的{abs(acq)/ocf*100:.0f}% —— 关注商誉与整合风险",
            f"FY{yr} spent ${abs(acq):,.0f}M on acquisitions (~{abs(acq)/ocf*100:.0f}% of OCF) — goodwill/integration risk")
    ret = abs(buyback or 0) + abs(dividend or 0)
    if ret > 0 and fcf is not None:
        if fcf > 0 and ret > fcf and (debt_net or 0) > 0:
            add("warn", f"FY{yr} 回购+分红(${ret:,.0f}M)超过自由现金流且当年净举债 —— 借钱回馈股东, 不可持续",
                f"FY{yr} buybacks+dividends (${ret:,.0f}M) exceed FCF with net new borrowing — unsustainable")
        elif fcf > 0 and ret > 0.9 * fcf:
            add("info", f"FY{yr} 回购+分红(${ret:,.0f}M)几乎用尽自由现金流",
                f"FY{yr} buybacks+dividends (${ret:,.0f}M) nearly exhaust free cash flow")
        elif fcf > 0:
            add("good", f"FY{yr} 以${ret:,.0f}M回馈股东(回购${abs(buyback or 0):,.0f}M+分红${abs(dividend or 0):,.0f}M), 自由现金流覆盖充分",
                f"FY{yr} returned ${ret:,.0f}M to shareholders, well covered by FCF")
    return notes


def _options(tk, price):
    try:
        exps = tk.options
    except Exception:
        exps = None
    if not exps:
        return None
    exp = exps[0]
    try:
        oc = ds.call_with_retry(tk.option_chain, exp)
    except Exception:
        return None
    calls, puts = oc.calls, oc.puts
    call_oi = float(calls["openInterest"].fillna(0).sum())
    put_oi = float(puts["openInterest"].fillna(0).sum())
    call_vol = float(calls["volume"].fillna(0).sum())
    put_vol = float(puts["volume"].fillna(0).sum())
    # 最大痛点: 使全部期权持有者内在价值之和最小的行权价
    strikes = sorted(set(list(calls["strike"]) + list(puts["strike"])))
    coi = dict(zip(calls["strike"], calls["openInterest"].fillna(0)))
    poi = dict(zip(puts["strike"], puts["openInterest"].fillna(0)))
    best_k, best_loss = None, None
    for K in strikes:
        loss = sum(coi.get(s, 0) * (K - s) for s in strikes if s < K) + \
               sum(poi.get(s, 0) * (s - K) for s in strikes if s > K)
        if best_loss is None or loss < best_loss:
            best_loss, best_k = loss, K
    return {"expiry": exp,
            "pc_oi": _r(put_oi / call_oi, 2) if call_oi else None,
            "pc_vol": _r(put_vol / call_vol, 2) if call_vol else None,
            "max_pain": _r(best_k, 2),
            "call_oi": int(call_oi), "put_oi": int(put_oi)}


def _news(tk, top=12):
    try:
        raw = tk.news or []
    except Exception:
        raw = []
    out = []
    for n in raw[:top]:
        c = n.get("content") if isinstance(n.get("content"), dict) else n
        title = c.get("title") or n.get("title")
        if not title:
            continue
        prov = c.get("provider") or {}
        pub = prov.get("displayName") if isinstance(prov, dict) else (n.get("publisher") or "")
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            u = c.get(key)
            if isinstance(u, dict) and u.get("url"):
                url = u["url"]; break
        url = url or n.get("link") or ""
        tm = c.get("pubDate") or c.get("displayTime") or ""
        low = title.lower()
        pos = sum(1 for k in _POS if k in low); neg = sum(1 for k in _NEG if k in low)
        tone = "利好" if pos > neg else ("利空" if neg > pos else "中性")
        out.append({"tone": tone, "title": title, "url": url,
                    "publisher": pub or "", "time": str(tm)[:19].replace("T", " ")})
    return out


def pull_profile_us(sym: str, name: str | None = None, sector: str | None = None,
                    finra_map: dict | None = None) -> dict:
    p = {"code": sym, "name": name, "market": "US",
         "summary": None, "business": None, "sector": sector, "website": None, "hq": None,
         "employees": None, "officers": [], "segments": None, "revenue": None,
         "cashflow": None, "cash_notes": [], "risk": None, "news": [],
         "options": None, "darkpool": None, "price": None}
    tk = ds.ticker(sym)
    info = _safe(lambda: tk.info) or {}

    p["name"] = name or info.get("shortName") or info.get("longName") or sym
    p["summary"] = info.get("longBusinessSummary")
    p["business"] = info.get("industry")
    p["sector"] = sector or info.get("sector")
    p["website"] = info.get("website")
    p["hq"] = ", ".join(x for x in (info.get("city"), info.get("state"), info.get("country")) if x) or None
    p["employees"] = info.get("fullTimeEmployees")
    p["officers"] = _officers(info)
    p["price"] = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
    # 卡片/速览通用指标 (与 A 股口径对齐: %)
    gm = _num(info.get("grossMargins")); roe = _num(info.get("returnOnEquity"))
    p["gross_margin"] = round(gm * 100, 1) if gm is not None else None
    p["roe"] = round(roe * 100, 1) if roe is not None else None
    p["current_ratio"] = _num(info.get("currentRatio"))

    # 风险: ISS 治理 + 做空/偿债
    spf = _num(info.get("shortPercentOfFloat"))
    if spf is not None:
        spf = round(spf * 100.0, 2) if spf <= 5 else round(spf, 2)
    inst = _num(info.get("heldPercentInstitutions"))
    p["risk"] = {
        "governance": {"audit": info.get("auditRisk"), "board": info.get("boardRisk"),
                       "compensation": info.get("compensationRisk"),
                       "shareholder": info.get("shareHolderRightsRisk"),
                       "overall": info.get("overallRisk")},
        "short": {"pct_float": spf, "days_to_cover": _num(info.get("shortRatio")),
                  "inst_held_pct": round(inst * 100, 1) if inst is not None else None,
                  "current_ratio": _num(info.get("currentRatio")),
                  "debt_to_equity": _num(info.get("debtToEquity"))}}

    # 营收 / 现金流
    fin = _safe(lambda: tk.income_stmt)
    qfin = _safe(lambda: tk.quarterly_income_stmt)
    rdates, rvals = _row(fin, ["Total Revenue"])
    ndates, nvals = _row(fin, ["Net Income", "Net Income Common Stockholders"])
    rev = None
    if rvals:
        yoy = [None] + [(_r((rvals[i] / rvals[i - 1] - 1) * 100, 1) if (rvals[i] and rvals[i - 1]) else None)
                        for i in range(1, len(rvals))]
        nyoy = None
        if nvals:
            nyoy = [None] + [(_r((nvals[i] / nvals[i - 1] - 1) * 100, 1) if (nvals[i] and nvals[i - 1]) else None)
                             for i in range(1, len(nvals))]
        qdates, qvals = _row(qfin, ["Total Revenue"])
        quarters, qrev = [], []
        if qvals:
            qcols = list(qfin.columns)[::-1][-8:]
            qser = qfin.loc["Total Revenue"] if "Total Revenue" in qfin.index else None
            for c in qcols:
                v = _num(qser[c]) if qser is not None else None
                quarters.append(str(c)[:7]); qrev.append(_r(v / M, 0) if v is not None else None)
        rev = {"years": rdates or [], "revenue": [_r(x, 0) for x in rvals],
               "rev_yoy": yoy, "net": [_r(x, 0) for x in nvals] if nvals else [],
               "net_yoy": nyoy or [], "quarters": quarters, "q_revenue": qrev}
    p["revenue"] = rev
    p["segments"] = _segments(fin, rdates, rvals)

    cfdf = _safe(lambda: tk.cashflow)
    if isinstance(cfdf, pd.DataFrame) and not cfdf.empty:
        yrs, ocf = _row(cfdf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
        _, fcf = _row(cfdf, ["Free Cash Flow"])
        _, capex = _row(cfdf, ["Capital Expenditure"])
        _, acq = _row(cfdf, ["Purchase Of Business", "Net Business Purchase And Sale"])
        _, buyback = _row(cfdf, ["Repurchase Of Capital Stock", "Common Stock Payments"])
        _, dividend = _row(cfdf, ["Cash Dividends Paid", "Common Stock Dividend Paid"])
        _, diss = _row(cfdf, ["Issuance Of Debt", "Long Term Debt Issuance"])
        _, drep = _row(cfdf, ["Repayment Of Debt", "Long Term Debt Payments"])
        _, cni = _row(cfdf, ["Net Income From Continuing Operations", "Net Income"])
        if yrs:
            cf = {"years": yrs,
                  "ocf": [_r(x, 0) for x in (ocf or [])],
                  "fcf": [_r(x, 0) for x in (fcf or [])],
                  "capex": [_r(x, 0) for x in (capex or [])],
                  "acq": [_r(x, 0) for x in (acq or [])],
                  "buyback": [_r(x, 0) for x in (buyback or [])],
                  "dividend": [_r(x, 0) for x in (dividend or [])],
                  "debt_iss": [_r(x, 0) for x in (diss or [])],
                  "debt_rep": [_r(x, 0) for x in (drep or [])],
                  "net_income": [_r(x, 0) for x in (cni or nvals or [])]}
            p["cashflow"] = cf
            p["cash_notes"] = _cash_insights(cf)

    p["news"] = _news(tk)
    p["options"] = _options(tk, p["price"])

    fm = finra_map if finra_map is not None else ds.finra_short_map()
    p["darkpool"] = fm.get(sym) or fm.get(sym.replace(".", "/"))
    return p
