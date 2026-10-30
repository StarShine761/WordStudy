# -*- coding: utf-8 -*-
"""把中文释义按词性拆行显示。

词库里的释义通常长这样::

    深刻的
    adj. 深切的，深远的；知识渊博的

但有些条目把多个词性挤在同一行::

    adj.昏暗的，朦胧的vt.使…模糊不清n.某种东西

本模块负责把同一行里的 ``n.`` / ``v.`` / ``adj.`` 等词性拆开，
每个词性单独一行，并统一成 ``词性. 释义`` 的格式。同一种词性如果
出现多次，会合并到同一个词性行里，避免重复。

注意：像 ``【同义】goods n. 货物`` 这种嵌在注释/括号里的词性不会被拆开，
否则会把一条释义拆成很多无意义的碎片。
"""

import re
import unicodedata

# 原始词性缩写 -> 归一化后的键
_POS_KEYS = {
    "n": "n",
    "v": "v",
    "vt": "vt",
    "vi": "vi",
    "adj": "adj",
    "a": "adj",       # 有些词库用 a. 表示 adjective
    "adv": "adv",
    "ad": "adv",      # 有些词库用 ad. 表示 adverb
    "prep": "prep",
    "conj": "conj",
    "pron": "pron",
    "num": "num",
    "art": "art",
    "int": "int",
    "interj": "interj",
    "aux": "aux",
    "modal": "aux",
    "abbr": "abbr",
    "pl": "pl",
    "phr": "phr",
    "pref": "pref",
    "quant": "quant",
}

# 归一化后的键 -> 展示用的词性标签
_POS_LABELS = {
    "n": "n.",
    "v": "v.",
    "vt": "vt.",
    "vi": "vi.",
    "adj": "adj.",
    "adv": "adv.",
    "prep": "prep.",
    "conj": "conj.",
    "pron": "pron.",
    "num": "num.",
    "art": "art.",
    "int": "int.",
    "interj": "interj.",
    "aux": "aux.",
    "abbr": "abbr.",
    "pl": "pl.",
    "phr": "phr.",
    "pref": "pref.",
    "quant": "quant.",
}

# 长的缩写放前面，避免 "num." 先匹配到 "n." 之类的问题。
_POS_RE = re.compile(
    r"(?P<pos>interj|modal|prep|conj|pron|abbr|quant|phr|pref|aux|adj|adv|"
    r"art|num|int|vt|vi|pl|ad|n|v|a)\.",
    re.IGNORECASE,
)

_OPEN_BRACKETS = "([{（【《"
_CLOSE_BRACKETS = ")]}）】》"


def _display_width(text):
    """按终端显示宽度计算（中文/emoji 算 2 列）。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _is_boundary(text, start):
    """判断 start 处的词性是否是一个新词性段的开始。

    前面如果是英文字母/数字（例如 ``goods n.``），说明这是注释里的
    嵌套词性，不应该拆开。
    """
    index = start - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    if index < 0:
        return True
    prev = text[index]
    if prev.isascii() and (prev.isalnum() or prev in "'’-"):
        return False
    return True


def _bracket_pairs(text):
    """返回行内成对括号的位置。

    词库里偶尔会有漏写右括号的条目（例如 ``人(或物vt.破坏``）。这种
    没有配对的左括号不能算作有效作用域，否则它后面的词性就永远拆不出来。
    """
    stack = []
    opens = set()
    closes = set()
    for index, char in enumerate(text):
        if char in _OPEN_BRACKETS:
            stack.append(index)
        elif char in _CLOSE_BRACKETS and stack:
            start = stack.pop()
            opens.add(start)
            closes.add(index)
    return opens, closes


def _top_level_positions(text):
    """返回一行里处于最外层（不在成对括号内）的词性起始位置。"""
    positions = []
    depth = 0
    index = 0
    length = len(text)
    opens, closes = _bracket_pairs(text)
    while index < length:
        char = text[index]
        if index in opens:
            depth += 1
        elif index in closes:
            depth = max(0, depth - 1)
        elif depth == 0 and char.isalpha():
            match = _POS_RE.match(text, index)
            if match and _is_boundary(text, index):
                positions.append(index)
                index = match.end()
                continue
        index += 1
    return positions


def _split_line(line):
    """把一行拆成 [(词性键或 None, 内容), ...]。"""
    positions = _top_level_positions(line)
    if not positions:
        return [(None, line.strip())]

    segments = []
    prefix = line[:positions[0]].strip()
    if prefix:
        segments.append((None, prefix))

    for order, start in enumerate(positions):
        end = positions[order + 1] if order + 1 < len(positions) else len(line)
        segment = line[start:end].strip()
        if not segment:
            continue
        match = _POS_RE.match(segment)
        if not match:
            segments.append((None, segment))
            continue
        key = _POS_KEYS.get(match.group("pos").lower())
        if not key:
            segments.append((None, segment))
            continue
        segments.append((key, segment[match.end():].strip()))
    return segments


def meaning_lines(text):
    """把释义格式化成按词性分行的列表。

    同一词性出现多次时合并为一行；没有词性的句子/短语保持原样。
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    entries = []          # [(key, content), ...]
    index_by_key = {}
    seen = set()

    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        for key, content in _split_line(line):
            content = content.strip()
            if key is None:
                entries.append((None, content))
                continue
            if key in index_by_key:
                old_index = index_by_key[key]
                old_content = entries[old_index][1]
                if content and content != old_content:
                    old_parts = old_content.split("；")
                    if content not in old_parts:
                        entries[old_index] = (
                            key,
                            f"{old_content}；{content}" if old_content else content,
                        )
                continue
            index_by_key[key] = len(entries)
            entries.append((key, content))

    lines = []
    for key, content in entries:
        if key is None:
            line = content
        else:
            label = _POS_LABELS[key]
            line = f"{label} {content}".rstrip() if content else label
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def format_meaning(text, first_prefix="", cont_prefix=None):
    """格式化释义；第一行用 first_prefix，后续行用 cont_prefix 缩进。"""
    lines = meaning_lines(text) or ["暂无释义"]
    if cont_prefix is None:
        cont_prefix = " " * _display_width(first_prefix)
    return "\n".join(
        (first_prefix if position == 0 else cont_prefix) + line
        for position, line in enumerate(lines)
    )


def format_option(label, text, indent="  ", prefix=""):
    """格式化一个选项：选项编号在第一行，后续词性行与释义起始位置对齐。"""
    lines = meaning_lines(text) or ["暂无释义"]
    head = f"{indent}{prefix}{label}. "
    cont = " " * _display_width(head)
    return "\n".join(
        [head + lines[0]] + [cont + line for line in lines[1:]]
    )
