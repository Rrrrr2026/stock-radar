#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股数据访问层 (self-contained yfinance layer)
==============================================
Yahoo Finance (yfinance) + FINRA 场外空头成交(暗池代理)。轻量重试 + FINRA 文件进程内缓存。
"""
from __future__ import annotations
import time
import logging
import datetime as dt

log = logging.getLogger("radar.datasource_us")

RETRY = 3
BACKOFF = 1.0


def yf():
    import yfinance
    return yfinance


def call_with_retry(fn, *a, **k):
    last = None
    for i in range(RETRY):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa
            last = e
            time.sleep(BACKOFF * (2 ** i))
    raise last


def ticker(sym: str):
    return yf().Ticker(sym)


# ---- FINRA 每日场外(含暗池)空头成交占比 —— 公开数据中最常用的"暗池活动"代理 ----
_FINRA_CACHE = {"date": None, "map": None}


def finra_short_map(max_back: int = 6) -> dict:
    """回溯至多 max_back 天, 取最近一份 FINRA 每日合并空头成交文件, 解析成 {SYMBOL: pct}。
    整份文件全市场共用, 只下载一次。"""
    if _FINRA_CACHE["map"] is not None:
        return _FINRA_CACHE["map"]
    import requests
    today = dt.date.today()
    out = {}
    for back in range(max_back):
        d = (today - dt.timedelta(days=back)).strftime("%Y%m%d")
        url = f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt"
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or "|" not in r.text[:200]:
                continue
            lines = r.text.strip().splitlines()
            header = lines[0].split("|")
            idx = {name: i for i, name in enumerate(header)}
            si, ti, yi = idx.get("Symbol"), idx.get("TotalVolume"), idx.get("ShortVolume")
            if None in (si, ti, yi):
                continue
            for ln in lines[1:]:
                p = ln.split("|")
                if len(p) <= max(si, ti, yi):
                    continue
                sym = p[si].strip()
                try:
                    tv = float(p[ti]); sv = float(p[yi])
                except Exception:
                    continue
                if tv > 0 and sym:
                    out[sym] = {"short_pct": round(sv / tv * 100, 2), "date": d}
            if out:
                _FINRA_CACHE.update(date=d, map=out)
                log.info("FINRA 场外空头: %d 只 (%s)", len(out), d)
                return out
        except Exception as e:
            log.debug("FINRA %s 失败: %s", d, e)
    _FINRA_CACHE.update(date=None, map={})
    return {}
