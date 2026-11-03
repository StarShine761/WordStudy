# -*- coding: utf-8 -*-
"""联网查词：调用有道词典公开接口，返回中文释义与音标。

仅使用标准库，供阅读器在离线词库没有释义时补全。
"""

import html
import json
import re
import urllib.parse
import urllib.request

API_URL = "https://dict.youdao.com/jsonapi?q={}"
TIMEOUT = 4.0

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


def fetch(word, timeout=TIMEOUT):
    """联网查询单词。查不到返回 None，网络异常抛 OnlineLookupError。"""
    query = str(word or "").strip()
    if not query:
        return None
    request = urllib.request.Request(
        API_URL.format(urllib.parse.quote(query)),
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:
        raise OnlineLookupError(str(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise OnlineLookupError("接口返回内容无法解析") from exc
    return parse_word_payload(data, query)
