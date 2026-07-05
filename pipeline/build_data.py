#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成仪表盘数据 (docs/data.js)
=============================
    python -m pipeline.build_data                # 用默认关注池
    python -m pipeline.build_data 600519 000001  # 只拉指定代码
拉取每只股票的深度档案, 写成 docs/data.js -> window.__RADAR__ = {...};
仪表盘 docs/index.html 用 <script src="data.js"> 直接读取, 双击/GitHub Pages 均可打开。
"""
from __future__ import annotations
import os
import sys
import json
import logging
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from . import profile as pf
from .watchlist import WATCHLIST

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("radar.build")

_HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(_HERE, "..", "docs")
DISCLAIMER = ("本页仅对公开财务/行情数据做自动化整理与展示, 不构成任何投资建议; "
              "暗池以大宗交易近似、期权博弈以融资融券近似, 均为 A 股代理指标; "
              "所有数据需人工复核, 使用者自负盈亏与风控。")


def _card(p: dict) -> dict:
    """从完整档案中提炼列表卡片所需的速览字段。"""
    rev = p.get("revenue") or {}
    risk = p.get("risk") or {}
    seg = p.get("segments") or {}
    news = p.get("news") or []

    def last(arr):
        arr = [x for x in (arr or []) if x is not None]
        return arr[-1] if arr else None

    top_seg = None
    if seg.get("items"):
        it = seg["items"][0]
        top_seg = {"name": it.get("name"), "pct": it.get("pct"), "yoy": it.get("yoy")}
    return {
        "code": p["code"], "name": p.get("name"), "sector": p.get("sector"),
        "revenue": last(rev.get("revenue")), "rev_yoy": last(rev.get("rev_yoy")),
        "net_yoy": last(rev.get("net_yoy")),
        "gross_margin": risk.get("gross_margin"), "roe": risk.get("roe"),
        "debt_ratio": risk.get("debt_ratio"),
        "top_segment": top_seg,
        "news_pos": sum(1 for n in news if n.get("tone") == "利好"),
        "news_neg": sum(1 for n in news if n.get("tone") == "利空"),
        "has_profile": True,
    }


def build(targets):
    profiles, cards = {}, []

    # 顺序执行(单线程): 同花顺(THS)接口经 py_mini_racer(V8) 解密, V8 引擎不能跨线程进入,
    # 多线程会触发原生崩溃。全市场数据(大宗/龙虎榜/两融)已由 profile 内的 memo 只下载一次,
    # 因此单线程的额外耗时主要是每股的限频 sleep, 对几十只的关注池完全可接受。
    log.info("拉取 %d 只个股深度档案 (单线程) ...", len(targets))
    for i, (code, name, sector) in enumerate(targets, 1):
        try:
            p = pf.pull_profile(code, sector=sector, name=name)
            profiles[code] = p
            log.info("  [%d/%d] %s OK", i, len(targets), code)
        except Exception as e:
            log.warning("  [%d/%d] %s 失败: %s", i, len(targets), code, e)

    # 保持关注池顺序
    order = [c for (c, _, _) in targets]
    for code in order:
        if code in profiles:
            cards.append(_card(profiles[code]))

    payload = {
        "meta": {
            "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(cards), "market": "A", "disclaimer": DISCLAIMER,
        },
        "stocks": cards,
        "profiles": profiles,
    }
    os.makedirs(DOCS, exist_ok=True)
    out = os.path.join(DOCS, "data.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write("window.__RADAR__ = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    log.info("已写出 %s (%d 只)", out, len(cards))
    return out


def main():
    args = sys.argv[1:]
    if args:
        by_code = {c: (c, n, s) for (c, n, s) in WATCHLIST}
        targets = [by_code.get(a, (a, None, None)) for a in args]
    else:
        targets = list(WATCHLIST)
    build(targets)


if __name__ == "__main__":
    main()
