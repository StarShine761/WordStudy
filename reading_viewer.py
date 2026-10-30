#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""终端阅读器：左侧原文，右侧释义栏，双击单词显示中文释义。"""

import ctypes
import os
import re
import sqlite3
import sys
import textwrap
import time
import unicodedata
from ctypes import wintypes

from meaning_format import meaning_lines

DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "word_learn.db")

ENCRYPTED_RE = re.compile(r"^[A-Za-z0-9+/=]{20,}$")

S_RESET = "\x1b[0m"
S_DIM = "\x1b[2m"
S_REV = "\x1b[7m"
S_CYAN = "\x1b[1;36m"
S_YELLOW = "\x1b[1;33m"
S_GREY = "\x1b[90m"

ALT_ON = ("\x1b[?1049h\x1b[?7l\x1b[?25l\x1b[?1000h\x1b[?1006h"
          "\x1b[2J\x1b[H")
ALT_OFF = "\x1b[?1006l\x1b[?1000l\x1b[0m\x1b[?25h\x1b[?7h\x1b[?1049l"

SGR_MOUSE_RE = re.compile(r"^\x1b\[<(\d+);(\d+);(\d+)([Mm])$")

STD_INPUT = -10
STD_OUTPUT = -11
ENABLE_PROCESSED_INPUT = 0x0001
ENABLE_LINE_INPUT = 0x0002
ENABLE_ECHO_INPUT = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
ENABLE_MOUSE_INPUT = 0x0010
ENABLE_QUICK_EDIT_MODE = 0x0040
ENABLE_EXTENDED_FLAGS = 0x0080
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

KEY_EVENT = 0x0001
MOUSE_EVENT = 0x0002
WINDOW_BUFFER_SIZE_EVENT = 0x0004
MOUSE_MOVED = 0x0001
DOUBLE_CLICK = 0x0002
MOUSE_WHEELED = 0x0004

VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_UP = 0x26
VK_DOWN = 0x28

LEFT_BUTTON = 0x0001
RIGHT_BUTTON = 0x0002


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", ctypes.c_short), ("Top", ctypes.c_short),
                ("Right", ctypes.c_short), ("Bottom", ctypes.c_short)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD), ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD),
                ("wVirtualKeyCode", wintypes.WORD), ("wVirtualScanCode", wintypes.WORD),
                ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class MOUSE_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("dwMousePosition", COORD), ("dwButtonState", wintypes.DWORD),
                ("dwControlKeyState", wintypes.DWORD), ("dwEventFlags", wintypes.DWORD)]


class WINDOW_BUFFER_SIZE_RECORD(ctypes.Structure):
    _fields_ = [("dwSize", COORD)]


class MENU_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("dwCommandId", wintypes.UINT)]


class FOCUS_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bSetFocus", wintypes.BOOL)]


class _EVENT(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("MouseEvent", MOUSE_EVENT_RECORD),
                ("WindowBufferSizeEvent", WINDOW_BUFFER_SIZE_RECORD),
                ("MenuEvent", MENU_EVENT_RECORD), ("FocusEvent", FOCUS_EVENT_RECORD)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT)]


_kernel32 = None
if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    _kernel32.GetStdHandle.restype = wintypes.HANDLE
    _kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetConsoleMode.restype = wintypes.BOOL
    _kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.SetConsoleMode.restype = wintypes.BOOL
    _kernel32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
    _kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    _kernel32.ReadConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD),
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.ReadConsoleInputW.restype = wintypes.BOOL


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(text):
    return sum(char_width(ch) for ch in text)


def clip_display(text, width):
    out = []
    used = 0
    for ch in str(text):
        w = char_width(ch)
        if used + w > width:
            if out:
                out[-1] = "…"
            return "".join(out)
        out.append(ch)
        used += w
    return "".join(out)


def wrap_display(text, width):
    """按显示宽度折行，英文单词尽量不拆开。"""
    width = max(1, width)
    lines = []
    for para in str(text).replace("\r", "").split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        cur = ""
        used = 0
        for token in re.findall(r"[A-Za-z0-9'’\-]+|.", para):
            if token == " ":
                if used + 1 <= width:
                    cur += " "
                    used += 1
                continue
            tw = disp_width(token)
            if used + tw > width and cur.strip():
                lines.append(cur.rstrip())
                cur, used = "", 0
            if tw > width:
                for ch in token:
                    w = char_width(ch)
                    if used + w > width and cur:
                        lines.append(cur)
                        cur, used = "", 0
                    cur += ch
                    used += w
            else:
                cur += token
                used += tw
        lines.append(cur.rstrip())
    return lines


def fit(text, width, style=""):
    text = clip_display(str(text), width)
    pad = " " * max(0, width - disp_width(text))
    return f"{style}{text}{pad}{S_RESET}" if style else text + pad


class Dictionary:
    def __init__(self, path=DICT_PATH):
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.cache = {}

    def close(self):
        self.conn.close()

    def lookup(self, word):
        key = word.lower()
        if key in self.cache:
            return self.cache[key]
        entry = None
        fallback = None
        for candidate in self._candidates(key):
            found = self._query(candidate)
            if not found:
                continue
            if found["meanings"]:
                entry = found
                break
            fallback = fallback or found
        self.cache[key] = entry or fallback
        return self.cache[key]

    def _query(self, word):
        row = self.conn.execute(
            "SELECT vc_vocabulary, vc_phonetic_uk, vc_phonetic_us, vc_interpretation "
            "FROM VOC_TB WHERE vc_vocabulary = ? COLLATE NOCASE LIMIT 1",
            (word,),
        ).fetchone()
        if not row:
            return None
        raw = (row[3] or "").strip()
        if ENCRYPTED_RE.fullmatch(raw):
            return {"word": row[0], "uk": (row[1] or "").strip(),
                    "us": (row[2] or "").strip(), "meanings": []}
        # 每个词性单独一行显示（n. / v. / adj. ...）
        meanings = meaning_lines(raw)
        return {
            "word": row[0],
            "uk": (row[1] or "").strip(),
            "us": (row[2] or "").strip(),
            "meanings": meanings,
        }

    @staticmethod
    def _candidates(word):
        w = word.strip("'-").lower()
        out = [w]
        if w.endswith("'s") or w.endswith("’s"):
            out.append(w[:-2])
        if w.endswith("ies"):
            out.append(w[:-3] + "y")
        if w.endswith("ied"):
            out.append(w[:-3] + "y")
        if w.endswith("es"):
            out.append(w[:-2])
        if w.endswith("s") and not w.endswith("ss"):
            out.append(w[:-1])
        for suffix in ("ed", "ing"):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                stem = w[:-len(suffix)]
                extra = [stem + "e"]
                if len(stem) > 2 and stem[-1] == stem[-2]:
                    extra.append(stem[:-1])
                extra.append(stem)
                out.extend(extra)
        if w.endswith("ily"):
            out.append(w[:-3] + "y")
        elif w.endswith("ly"):
            out.append(w[:-2])
        seen = set()
        return [x for x in out if x and not (x in seen or seen.add(x))]


class ConsoleReader:
    HEADER_ROWS = 2
    FOOTER_ROWS = 1

    def __init__(self, title, body_text, dictionary):
        self.title = f"阅读 | {title}"
        self.body = body_text
        self.dictionary = dictionary

        self.hin = None
        self.hout = None
        self._in_mode = None
        self._out_mode = None
        self._alt = False

        self.width = 0
        self.height = 0
        self.origin = (0, 0)
        self.panel_w = 0
        self.left_w = 0
        self.gap_w = 0
        self.body_h = 0
        self.lines = []
        self.top = 0
        self.panel = []
        self.sel = None
        self.panel_word = ""
        self.panel_entry = None
        self.dirty = True
        self.too_small = False
        self._last_click = (0.0, -1, -1)
        self._vt_buf = ""

    # ------------------------------------------------------------ 生命周期

    def run(self):
        try:
            self._open()
            self._resize()
            while True:
                if self.dirty:
                    self._draw()
                if not self._wait_events():
                    break
        finally:
            self._close()

    def _open(self):
        self.hin = _kernel32.GetStdHandle(STD_INPUT)
        self.hout = _kernel32.GetStdHandle(STD_OUTPUT)
        invalid = (None, 0, ctypes.c_void_p(-1).value)
        if self.hin in invalid or self.hout in invalid:
            raise RuntimeError("当前环境没有可用的控制台")
        in_mode, out_mode = wintypes.DWORD(), wintypes.DWORD()
        if not _kernel32.GetConsoleMode(self.hin, ctypes.byref(in_mode)):
            raise RuntimeError("标准输入不是交互式终端")
        if not _kernel32.GetConsoleMode(self.hout, ctypes.byref(out_mode)):
            raise RuntimeError("标准输出不是交互式终端")
        self._in_mode = in_mode.value
        self._out_mode = out_mode.value
        new_in = (in_mode.value | ENABLE_MOUSE_INPUT | ENABLE_WINDOW_INPUT | ENABLE_EXTENDED_FLAGS) & ~(
            ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT | ENABLE_QUICK_EDIT_MODE)
        if not _kernel32.SetConsoleMode(self.hin, new_in):
            raise RuntimeError("无法启用控制台鼠标输入")
        if not _kernel32.SetConsoleMode(
                self.hout, out_mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            raise RuntimeError("终端不支持 ANSI 转义")
        sys.stdout.write(ALT_ON)
        sys.stdout.flush()
        self._alt = True

    def _close(self):
        if self._alt:
            sys.stdout.write(ALT_OFF)
            sys.stdout.flush()
            self._alt = False
        if self.hin is not None and self._in_mode is not None:
            _kernel32.SetConsoleMode(self.hin, self._in_mode)
        if self.hout is not None and self._out_mode is not None:
            _kernel32.SetConsoleMode(self.hout, self._out_mode)

    # ------------------------------------------------------------ 输入

    def _wait_events(self):
        records = (INPUT_RECORD * 32)()
        while True:
            count = wintypes.DWORD()
            if not _kernel32.ReadConsoleInputW(self.hin, records, 32, ctypes.byref(count)):
                raise RuntimeError("读取终端输入失败")
            for i in range(count.value):
                if not self._handle(records[i]):
                    return False
            if self.dirty:
                return True

    def _handle(self, record):
        if record.EventType == KEY_EVENT:
            return self._handle_key(record.Event.KeyEvent)
        if record.EventType == MOUSE_EVENT:
            self._handle_mouse(record.Event.MouseEvent)
        elif record.EventType == WINDOW_BUFFER_SIZE_EVENT:
            self._resize()
        return True

    def _handle_key(self, key):
        if not key.bKeyDown:
            return True
        ch = key.uChar or ""
        if self._vt_buf or ch == "\x1b":
            return self._feed_vt(ch)
        return self._handle_plain(ch, key.wVirtualKeyCode)

    def _feed_vt(self, ch):
        self._vt_buf += ch
        buf = self._vt_buf
        if buf == "\x1b":
            return True
        if ch == "\x1b":
            # 未完成的序列被新的 ESC 打断，从头开始
            self._vt_buf = "\x1b"
            return True
        if buf.startswith("\x1b[<"):
            match = SGR_MOUSE_RE.match(buf)
            if match:
                self._vt_buf = ""
                self._vt_mouse(int(match.group(1)), int(match.group(2)) - 1,
                               int(match.group(3)) - 1, match.group(4))
            elif len(buf) > 24:
                self._vt_buf = ""
            return True
        if buf.startswith("\x1b[M"):
            if len(buf) >= 6:
                self._vt_buf = ""
                button, x, y = (ord(c) - 32 for c in buf[3:6])
                self._vt_mouse(button, x - 1, y - 1, "M")
            return True
        if buf.startswith("\x1b["):
            if len(buf) > 2 and "@" <= ch <= "~":
                self._vt_buf = ""
            return True
        self._vt_buf = ""
        self._clear_panel()
        return self._handle_plain(ch, 0)

    def _vt_mouse(self, button, x, y, kind):
        if kind == "m" or button & 32:
            return
        if button & 64:
            self._scroll(-3 if (button & 1) == 0 else 3)
            return
        if (button & 3) in (0, 2):
            self._click(x, y, double=False)

    def _handle_plain(self, ch, vk):
        if ch == "\x03" or (ch and ch.lower() == "q") or ch in ("\r", "\n"):
            return False
        if vk == VK_UP:
            self._scroll(-1)
        elif vk == VK_DOWN:
            self._scroll(1)
        elif vk == VK_PRIOR:
            self._scroll(-self.body_h)
        elif vk == VK_NEXT or ch == " ":
            self._scroll(self.body_h)
        elif vk == VK_HOME:
            self._scroll(-len(self.lines))
        elif vk == VK_END:
            self._scroll(len(self.lines))
        return True

    def _handle_mouse(self, mouse):
        flags = mouse.dwEventFlags
        x, y = mouse.dwMousePosition.X, mouse.dwMousePosition.Y
        if flags & MOUSE_WHEELED:
            delta = ctypes.c_short((mouse.dwButtonState >> 16) & 0xFFFF).value
            self._scroll(-3 if delta > 0 else 3)
        elif flags & MOUSE_MOVED:
            return
        elif flags & DOUBLE_CLICK:
            self._click(x, y, double=True)
        elif flags == 0 and mouse.dwButtonState & (LEFT_BUTTON | RIGHT_BUTTON):
            self._click(x, y, double=False)

    def _click(self, x, y, double):
        now = time.monotonic()
        if not double:
            last_time, last_x, last_y = self._last_click
            if (now - last_time <= 0.5 and abs(x - last_x) <= 1 and abs(y - last_y) <= 1):
                double = True
                self._last_click = (0.0, x, y)
            else:
                self._last_click = (now, x, y)
        else:
            self._last_click = (0.0, x, y)

        if not double:
            self._clear_panel()
            return

        row = y - self.origin[1] - self.HEADER_ROWS
        col = x - self.origin[0]
        if self.too_small or not (0 <= row < self.body_h and 0 <= col < self.left_w):
            self._clear_panel()
            return
        index = self.top + row
        if index >= len(self.lines):
            self._clear_panel()
            return
        picked = self._word_at(self.lines[index], col)
        if not picked:
            self._clear_panel()
            return
        word, start, end = picked
        self.sel = (index, start, end)
        self.panel_word = word
        self.panel_entry = self.dictionary.lookup(word)
        self._build_panel()
        self.dirty = True

    def _scroll(self, delta):
        max_top = max(0, len(self.lines) - self.body_h)
        new_top = min(max_top, max(0, self.top + delta))
        if new_top != self.top:
            self.top = new_top
            self.dirty = True

    def _clear_panel(self):
        self.sel = None
        self.panel_word = ""
        self.panel_entry = None
        self._build_panel()
        self.dirty = True

    # ------------------------------------------------------------ 布局

    def _resize(self):
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not _kernel32.GetConsoleScreenBufferInfo(self.hout, ctypes.byref(info)):
            raise RuntimeError("无法读取终端尺寸")
        width = info.srWindow.Right - info.srWindow.Left + 1
        height = info.srWindow.Bottom - info.srWindow.Top + 1
        self._apply_layout(width, height, (info.srWindow.Left, info.srWindow.Top))

    def _apply_layout(self, width, height, origin=(0, 0)):
        self.width = width
        self.height = height
        self.origin = origin
        if width < 50 or height < 10:
            self.too_small = True
            self.lines = []
            self.panel = []
            self.dirty = True
            return
        self.too_small = False
        if width >= 100:
            # 宽屏：正文约半屏，右侧四分之一固定为释义栏，中间留白
            left_w = width // 2
            panel_w = max(24, width // 4)
            gap_w = width - left_w - panel_w
        else:
            panel_w = min(36, max(24, width // 3))
            left_w = width - panel_w
            gap_w = 0
        body_h = height - self.HEADER_ROWS - self.FOOTER_ROWS
        relayout = left_w != self.left_w or body_h != self.body_h or not self.lines
        self.panel_w, self.left_w, self.body_h, self.gap_w = panel_w, left_w, body_h, gap_w
        if relayout:
            self.lines = self._wrap_body(left_w)
            if self.sel:
                self.sel = None
                self.panel_word = ""
                self.panel_entry = None
        self.top = max(0, min(self.top, max(0, len(self.lines) - body_h)))
        self._build_panel()
        self.dirty = True

    def _wrap_body(self, width):
        lines = []
        for para in str(self.body).replace("\r\n", "\n").split("\n"):
            para = para.strip()
            if not para:
                lines.append("")
                continue
            chunks = textwrap.wrap(para, width=max(10, width),
                                   break_long_words=True, break_on_hyphens=False) or [""]
            for chunk in chunks:
                if disp_width(chunk) > width:
                    lines.extend(wrap_display(chunk, width))
                else:
                    lines.append(chunk)
        while lines and not lines[-1]:
            lines.pop()
        return lines

    def _build_panel(self):
        inner = max(1, self.panel_w - 2)
        rows = []
        if self.sel:
            entry = self.panel_entry
            rows.append((self.panel_word, S_YELLOW))
            if entry:
                phonetics = " ".join(p for p in (
                    f"英 {entry['uk']}" if entry["uk"] else "",
                    f"美 {entry['us']}" if entry["us"] else "",
                ) if p)
                if phonetics:
                    rows.extend((line, S_GREY) for line in wrap_display(phonetics, inner))
            rows.append(("", ""))
            if entry and entry["meanings"]:
                for meaning in entry["meanings"]:
                    rows.extend((line, "") for line in wrap_display(meaning, inner))
                if entry["word"].lower() != self.panel_word.lower():
                    rows.append(("", ""))
                    rows.append((f"原形 {entry['word']}", S_GREY))
            elif entry:
                rows.append(("该词释义在词库中不可用", S_GREY))
            else:
                rows.append(("离线词库未收录", S_GREY))
        self.panel = rows[: self.body_h]

    # ------------------------------------------------------------ 绘制

    def _frame(self):
        if self.too_small:
            return [fit("窗口太小", self.width, S_CYAN),
                    fit("请放大终端后继续，按 q 退出", self.width, S_GREY)]
        rows = [
            fit(self.title, self.width, S_CYAN),
            fit("双击单词查释义 | 点击别处隐藏 | 方向键/PgUp/PgDn/滚轮翻页 | 回车/q 返回",
                self.width, S_GREY),
        ]
        for i in range(self.body_h):
            index = self.top + i
            line = self.lines[index] if index < len(self.lines) else ""
            rows.append(self._render_left(line, index) + " " * self.gap_w
                        + S_DIM + "│" + S_RESET + " " + self._render_panel(i))
        end = min(self.top + self.body_h, len(self.lines))
        rows.append(fit(f"第 {self.top + 1}-{end} 行 / 共 {len(self.lines)} 行",
                        self.width, S_GREY))
        return rows

    def _render_left(self, line, index):
        if self.sel and self.sel[0] == index:
            _, start, end = self.sel
            text = line[:start] + S_REV + line[start:end] + S_RESET + line[end:]
        else:
            text = line
        return text + " " * max(0, self.left_w - disp_width(line))

    def _render_panel(self, row):
        inner = max(1, self.panel_w - 2)
        text, style = ("", "")
        if row < len(self.panel):
            text, style = self.panel[row]
        text = clip_display(text, inner)
        pad = " " * max(0, inner - disp_width(text))
        return f"{style}{text}{pad}{S_RESET}" if style else text + pad

    def _draw(self):
        self.dirty = False
        rows = self._frame()
        sys.stdout.write("\x1b[H" + "\r\n".join(rows) + "\x1b[J")
        sys.stdout.flush()

    @staticmethod
    def _word_at(line, col):
        position = 0
        index = None
        for i, ch in enumerate(line):
            width = char_width(ch)
            if position <= col < position + width:
                index = i
                break
            position += width
        if index is None:
            return None

        def is_word_char(ch):
            return ch.isalpha() or ch in "'-"

        if not is_word_char(line[index]):
            return None
        start = index
        while start > 0 and is_word_char(line[start - 1]):
            start -= 1
        end = index + 1
        while end < len(line) and is_word_char(line[end]):
            end += 1
        while start < end and not line[start].isalpha():
            start += 1
        while end > start and not line[end - 1].isalpha():
            end -= 1
        if start >= end:
            return None
        return line[start:end], start, end


def show_passage(title, body_text):
    if _kernel32 is None:
        raise RuntimeError("仅支持 Windows 控制台")
    dictionary = Dictionary()
    try:
        ConsoleReader(title or "阅读原文", body_text, dictionary).run()
    finally:
        dictionary.close()
