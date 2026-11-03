# -*- coding: utf-8 -*-
"""联网查词：调用有道词典公开接口，返回中文释义与音标。

仅使用标准库，供阅读器在离线词库没有释义时补全。
"""

import html
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://dict.youdao.com/jsonapi?q={}"
SUGGEST_URL = ("https://dict.youdao.com/suggest?num=5&ver=3.0"
               "&doctype=json&cache=false&le=en&q={}")
TIMEOUT = 5.0

_MARKUP_RE = re.compile(
    r"</?(?:b|i|em|strong|span|br|sup|sub)(?:\s[^>]*)?>", re.IGNORECASE)


class OnlineLookupError(Exception):
    """网络不可用或接口返回异常。"""


def _clean(text):
    text = html.unescape(str(text))
    text = _MARKUP_RE.sub("", text)
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _phrase_text(value):
    """有道的 return-phrase 既可能是字符串，也可能是 {'l': {'i': ...}}。"""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        inner = value.get("l")
        if isinstance(inner, dict):
            return _clean(inner.get("i") or "")
    return ""


def parse_word_payload(data, query):
    """从 jsonapi 响应里提取释义；没有中文释义时返回 None。"""
    words = (data.get("ec") or {}).get("word") or []
    if not words:
        return None
    info = words[0]
    meanings = []
    for trs in info.get("trs") or []:
        for tr in (trs or {}).get("tr") or []:
            items = ((tr or {}).get("l") or {}).get("i") or []
            if isinstance(items, str):
                items = [items]
            for item in items:
                text = _clean(item)
                if text and text not in meanings:
                    meanings.append(text)
    if not meanings:
        return None
    return {
        "word": _phrase_text(info.get("return-phrase")) or query,
        "uk": _clean(info.get("ukphone") or ""),
        "us": _clean(info.get("usphone") or ""),
        "meanings": meanings,
    }


def parse_suggest_payload(data, query):
    """从 suggest 响应里取完全匹配条目的释义（无音标）。"""
    entries = (data.get("data") or {}).get("entries") or []
    key = query.lower()
    for entry in entries:
        if str(entry.get("entry") or "").lower() != key:
            continue
        explain = _clean(entry.get("explain") or "")
        if explain:
            return {"word": entry.get("entry") or query,
                    "uk": "", "us": "", "meanings": [explain]}
    return None


def _request_json(url, timeout):
    request = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://dict.youdao.com/",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise OnlineLookupError(f"HTTP {exc.code}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise OnlineLookupError("请求超时") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise OnlineLookupError("请求超时") from exc
        raise OnlineLookupError("网络不可用") from exc
    except Exception as exc:
        raise OnlineLookupError("请求失败") from exc
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise OnlineLookupError("接口返回内容无法解析") from exc


def fetch(word, timeout=TIMEOUT):
    """联网查询单词。查不到返回 None，网络异常抛 OnlineLookupError。"""
    query = str(word or "").strip()
    if not query:
        return None
    quoted = urllib.parse.quote(query)

    last_error = None
    data = None
    for attempt in range(2):
        try:
            data = _request_json(API_URL.format(quoted), timeout)
            break
        except OnlineLookupError as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.4)

    if data is not None:
        last_error = None
        entry = parse_word_payload(data, query)
        if entry:
            return entry

    # 主接口没给出释义（或不可达）时，换轻量的 suggest 接口再试一次
    try:
        suggest = _request_json(SUGGEST_URL.format(quoted), timeout)
    except OnlineLookupError:
        if last_error:
            raise last_error
        return None
    entry = parse_suggest_payload(suggest, query)
    if entry:
        return entry
    if last_error:
        raise last_error
    return None
