#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成仪表盘数据
==============
    python -m pipeline.build_data                 # A股默认关注池 -> docs/data.js   (window.__RADAR__)
    python -m pipeline.build_data 600519 000858   # 只拉指定 A 股代码
    python -m pipeline.build_data us              # 美股默认关注池 -> docs/data_us.js (window.__RADAR_US__)
    python -m pipeline.build_data us AAPL MSFT    # 只拉指定美股
仪表盘 docs/index.html 同时加载两份数据, 顶部可切 A股/美股。
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

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("radar.build")

_HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(_HERE, "..", "docs")

DISC_A = ("本页仅对公开财务/行情数据做自动化整理与展示, 不构成任何投资建议; "
          "暗池以大宗交易近似、期权博弈以融资融券近似, 均为 A 股代理指标; 需人工复核, 自负盈亏。")
DISC_US = ("本页仅对公开财务/行情数据做自动化整理与展示, 不构成任何投资建议; "
           "期权博弈为真实期权持仓、暗池为 FINRA 场外空头成交占比; 需人工复核, 自负盈亏。")


def _last(arr):
    arr = [x for x in (arr or []) if x is not None]
    return arr[-1] if arr else None


def _card_common(p):
    rev = p.get("revenue") or {}
    seg = p.get("segments") or {}
    news = p.get("news") or []
    top_seg = None
    if seg.get("items"):
        it = seg["items"][0]
        top_seg = {"name": it.get("name"), "pct": it.get("pct"), "yoy": it.get("yoy")}
    return {
        "code": p["code"], "name": p.get("name"), "sector": p.get("sector"),
        "market": p.get("market", "A"),
        "revenue": _last(rev.get("revenue")), "rev_yoy": _last(rev.get("rev_yoy")),
        "net_yoy": _last(rev.get("net_yoy")), "top_segment": top_seg,
        "news_pos": sum(1 for n in news if n.get("tone") == "利好"),
        "news_neg": sum(1 for n in news if n.get("tone") == "利空"),
    }


def _card_a(p):
    c = _card_common(p); risk = p.get("risk") or {}
    c["gross_margin"] = risk.get("gross_margin"); c["roe"] = risk.get("roe")
    c["debt_ratio"] = risk.get("debt_ratio")
    return c


def _card_us(p):
    c = _card_common(p)
    c["gross_margin"] = p.get("gross_margin"); c["roe"] = p.get("roe")
    return c


def build(targets, market="A"):
    if market == "US":
        from . import profile_us as pf
        from . import datasource_us as dsu
        finra = dsu.finra_short_map()
        pull = lambda code, name, sector: pf.pull_profile_us(code, name=name, sector=sector, finra_map=finra)
        card = _card_us
        out_name, glob, disc, cur = "data_us.js", "window.__RADAR_US__", DISC_US, "US"
    else:
        from . import profile as pf
        pull = lambda code, name, sector: pf.pull_profile(code, sector=sector, name=name)
        card = _card_a
        out_name, glob, disc, cur = "data.js", "window.__RADAR__", DISC_A, "A"

    profiles, cards = {}, []
    log.info("[%s] 拉取 %d 只个股深度档案 (单线程) ...", cur, len(targets))
    for i, (code, name, sector) in enumerate(targets, 1):
        try:
            p = pull(code, name, sector)
            profiles[code] = p
            log.info("  [%d/%d] %s OK", i, len(targets), code)
        except Exception as e:
            log.warning("  [%d/%d] %s 失败: %s", i, len(targets), code, e)

    for (code, _, _) in targets:
        if code in profiles:
            cards.append(card(profiles[code]))

    payload = {
        "meta": {"updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "count": len(cards), "market": cur, "disclaimer": disc},
        "stocks": cards, "profiles": profiles,
    }
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, out_name)
    with open(out, "w", encoding="utf-8") as f:
        f.write(glob + " = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    log.info("[%s] 已写出 %s (%d 只)", cur, out, len(cards))
    return out


def main():
    args = sys.argv[1:]
    market = "A"
    if args and args[0].lower() in ("us", "--us", "美股"):
        market = "US"; args = args[1:]
    if market == "US":
        from .watchlist_us import WATCHLIST_US as WL
    else:
        from .watchlist import WATCHLIST as WL
    if args:
        by_code = {c: (c, n, s) for (c, n, s) in WL}
        targets = [by_code.get(a, (a, None, None)) for a in args]
    else:
        targets = list(WL)
    build(targets, market=market)


if __name__ == "__main__":
    main()
