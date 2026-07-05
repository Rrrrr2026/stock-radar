#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据访问层 (self-contained akshare access layer)
================================================
个股雷达 / Stock Radar 独立项目 —— 不依赖任何外部仓库。
封装 akshare: 浏览器UA注入 + 限频重试 + 本地缓存 + 列名匹配工具。
"""
from __future__ import annotations
import os
import time
import pickle
import hashlib
import logging

import pandas as pd

log = logging.getLogger("radar.datasource")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_HERE, "..", "data", "cache")
os.makedirs(_CACHE_DIR, exist_ok=True)

CONFIG = {
    "sleep_sec": 0.4,          # 每次 akshare 调用前 sleep, 温柔限频
    "max_retries": 3,
    "retry_backoff_sec": 1.2,
    "use_cache": True,
    "cache_ttl_hours": 12,
}

# ---------------------------------------------------------------------------
#  给所有 requests.Session 注入浏览器 UA (akshare 默认不带 UA, 部分东财端点会重置连接)
# ---------------------------------------------------------------------------
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _install_ua_patch():
    try:
        import requests
        if getattr(requests.sessions.Session, "_radar_ua_patched", False):
            return
        orig = requests.sessions.Session.__init__

        def patched(self, *a, **k):
            orig(self, *a, **k)
            try:
                self.headers.update({"User-Agent": _BROWSER_UA})
            except Exception:
                pass
        requests.sessions.Session.__init__ = patched
        requests.sessions.Session._radar_ua_patched = True
    except Exception as e:
        log.debug("UA patch failed: %s", e)


_install_ua_patch()


def _ak():
    import akshare as ak
    return ak


# ---------------------------------------------------------------------------
#  缓存
# ---------------------------------------------------------------------------
def _cache_key(name: str, *args) -> str:
    raw = name + "|" + "|".join(str(a) for a in args)
    return f"{name}_{hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]}"


def cache_load(key: str):
    if not CONFIG["use_cache"]:
        return None
    path = os.path.join(_CACHE_DIR, key + ".pkl")
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) / 3600.0 > CONFIG["cache_ttl_hours"]:
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def cache_save(key: str, obj) -> None:
    if not CONFIG["use_cache"]:
        return
    path = os.path.join(_CACHE_DIR, key + ".pkl")
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "wb") as f:
            pickle.dump(obj, f)
        os.replace(tmp, path)
    except Exception as e:
        log.debug("cache save failed %s: %s", key, e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------------------
#  带重试的调用 + 列名匹配
# ---------------------------------------------------------------------------
def call_with_retry(fn, *args, **kwargs):
    last = None
    for attempt in range(CONFIG["max_retries"]):
        try:
            time.sleep(CONFIG["sleep_sec"])
            return fn(*args, **kwargs)
        except Exception as e:  # noqa
            last = e
            wait = CONFIG["retry_backoff_sec"] * (2 ** attempt)
            log.debug("retry %d/%d: %s (sleep %.1fs)", attempt + 1, CONFIG["max_retries"], e, wait)
            time.sleep(wait)
    raise last


def pick_col(df: pd.DataFrame, candidates, contains: bool = False):
    cols = list(df.columns)
    for cand in candidates:
        if cand in cols:
            return cand
    if contains:
        for cand in candidates:
            for col in cols:
                if cand in str(col):
                    return col
    return None


def _sina_symbol(code: str) -> str:
    """'002594' -> 'sz002594' (北交所 bj / 沪 sh / 深 sz)。"""
    code = str(code).zfill(6)
    if code.startswith("920") or code.startswith(("8", "4")):
        return "bj" + code
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "3", "2")):
        return "sz" + code
    return "sh" + code
