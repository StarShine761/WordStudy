#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阅读答题界面：左侧整页（上原文、下题目与作答），右侧四分之一为释义栏。

原文与题目全部展开成一页，滚轮/翻页键滚动整页，不再分上下两个独立滚动区。
"""

import ctypes

from reading_viewer import (
    ConsoleReader, Dictionary, DOUBLE_CLICK, LEFT_BUTTON, MOUSE_MOVED, MOUSE_WHEELED,
    RIGHT_BUTTON, S_CYAN, S_DIM, S_GREY, S_RESET, S_REV, S_YELLOW,
    VK_DOWN, VK_END, VK_HOME, VK_NEXT, VK_PRIOR, VK_UP,
    clip_display, disp_width, fit, wrap_display,
)

S_GREEN = "\x1b[1;32m"
S_RED = "\x1b[1;31m"

VK_BACK = 0x08
VK_TAB = 0x09
ANSWER_MAX = 80


class ExamReader(ConsoleReader):
    HEADER_ROWS = 2
    FOOTER_ROWS = 1

    def __init__(self, title, body_text, dictionary, session):
        super().__init__(title, body_text, dictionary)
        self.session = session
        self.doc = []
        self.passage_n = 0
        self.block_starts = []
        self.summary_start = None
        self.answer = ""
        self.feedback = ""
        self.finished = False

    # ------------------------------------------------------------ 布局

    def _apply_layout(self, width, height, origin=(0, 0)):
        self.width = width
        self.height = height
        self.origin = origin
        if width < 80 or height < 18:
            self.too_small = True
            self.lines = []
            self.panel = []
            self.doc = []
            self.dirty = True
            return
        self.too_small = False
        if width >= 100:
            left_w = width // 2
            panel_w = max(24, width // 4)
            gap_w = width - left_w - panel_w
        else:
            panel_w = min(36, max(24, width // 3))
            left_w = width - panel_w
            gap_w = 0
        resized = left_w != self.left_w
        self.left_w, self.panel_w, self.gap_w = left_w, panel_w, gap_w
        self.body_h = height - self.HEADER_ROWS - self.FOOTER_ROWS
        if resized or not self.lines:
            self.lines = self._wrap_body(left_w)
            if self.sel:
                self.sel = None
                self.panel_word = ""
                self.panel_entry = None
        self._build_doc()
        self.top = max(0, min(self.top, max(0, len(self.doc) - self.body_h)))
        self._build_panel()
        self.dirty = True

    def _build_doc(self):
        w = max(1, self.left_w)
        self.passage_n = len(self.lines)
        doc = [(line, "") for line in self.lines]
        doc.append(("-" * w, S_DIM))
        self.block_starts = []
        for i, block in enumerate(self.session.blocks):
            self.block_starts.append(len(doc))
            doc.extend(self._block_lines(block, i == self.session.current))
            doc.append(("", ""))
        self.summary_start = None
        if self.finished:
            self.summary_start = len(doc)
            doc.extend(self._summary_lines())
        self.doc = doc

    def _block_lines(self, block, current):
        w = max(1, self.left_w)
        state = block["state"]
        status = ""
        if state == "ok":
            status = f"   ✓ 你:{block['user']}"
        elif state == "bad":
            status = (f"   ✗ 你:{block['user'] or '-'} "
                      f"正确:{'/'.join(block['expected']) or '-'}")
        elif state == "skip":
            status = "   – 跳过"
        head = f"{'▶' if current else '·'} {block['kind_label']} 第 {block['span']} 题{status}"
        if current:
            head_style = S_REV
        else:
            head_style = {"ok": S_GREEN, "bad": S_RED, "skip": S_GREY}.get(state, "")
        out = [(clip_display(head, w), head_style)]

        if block["multi"]:
            if block.get("instruction"):
                out.extend((ln, S_GREY) for ln in wrap_display(block["instruction"], w))
            if block.get("block_options"):
                out.append(("选项:", S_YELLOW))
                for option in block["block_options"]:
                    out.extend((ln, "") for ln in wrap_display(option, w))
            for item in block["items"]:
                out.append((clip_display(f"第 {item['number']} 题", w), S_YELLOW))
                out.extend((ln, "") for ln in wrap_display(item["prompt"], w))
        else:
            item = block["items"][0]
            out.extend((ln, "") for ln in wrap_display(item["prompt"], w))
            if block.get("instruction"):
                out.extend((ln, S_GREY) for ln in wrap_display(block["instruction"], w))
            if block.get("block_options"):
                out.append(("选项:", S_YELLOW))
                for option in block["block_options"]:
                    out.extend((ln, "") for ln in wrap_display(option, w))
        return out

    def _summary_lines(self):
        s = self.session
        w = max(1, self.left_w)
        lines = [(clip_display(f"🏁 成绩 {s.correct}/{s.answered} "
                               f"{s.score * 100:.0f}%", w), S_YELLOW), ("", "")]
        if s.wrong_items:
            lines.append(("错题:", S_RED))
            for item in s.wrong_items:
                lines.extend((ln, "") for ln in wrap_display(f"第 {item['num']} 题", w))
                lines.extend((ln, "") for ln in wrap_display(
                    f"你:{item['answer'] or '(空)'} 正确:{' / '.join(item['answers'])}", w))
        else:
            lines.append(("全部答对 🎉", S_GREEN))
        lines.extend([("", ""), ("回车/q 返回，可继续滚动查看", S_GREY)])
        return lines

    # ------------------------------------------------------------ 输入

    def _handle_plain(self, ch, vk):
        if ch == "\x03":
            self.session.abort()
            return False
        if self.finished:
            if ch in ("\r", "\n") or (ch and ch.lower() == "q"):
                return False
            if ch == "\t" or vk == VK_TAB:
                self._jump_to_current()
            else:
                self._handle_scroll_key(vk)
            return True
        if ch == "\t" or vk == VK_TAB:
            self._jump_to_current()
        elif vk in (VK_UP, VK_DOWN, VK_PRIOR, VK_NEXT, VK_HOME, VK_END):
            self._handle_scroll_key(vk)
        elif ch in ("\r", "\n"):
            self._submit()
        elif ch in ("\x08", "\x7f") or vk == VK_BACK:
            if self.answer:
                self.answer = self.answer[:-1]
                self.dirty = True
        elif ch and ch.isprintable():
            if len(self.answer) < ANSWER_MAX:
                self.answer += ch
                self.feedback = ""
                self.dirty = True
        return True

    def _handle_scroll_key(self, vk):
        if vk == VK_UP:
            self._scroll(-1)
        elif vk == VK_DOWN:
            self._scroll(1)
        elif vk == VK_PRIOR:
            self._scroll(-self.body_h)
        elif vk == VK_NEXT:
            self._scroll(self.body_h)
        elif vk == VK_HOME:
            self._scroll(-10 ** 6)
        elif vk == VK_END:
            self._scroll(10 ** 6)

    def _scroll(self, delta):
        max_top = max(0, len(self.doc) - self.body_h)
        new_top = min(max_top, max(0, self.top + delta))
        if new_top != self.top:
            self.top = new_top
            self.dirty = True

    def _jump_to(self, index):
        new_top = max(0, min(index, max(0, len(self.doc) - self.body_h)))
        if new_top != self.top:
            self.top = new_top
            self.dirty = True

    def _jump_to_current(self):
        if self.finished:
            start = self.summary_start
        elif self.session.blocks:
            start = self.block_starts[self.session.current]
        else:
            start = 0
        if start is not None:
            self._jump_to(start)

    def _submit(self):
        text = self.answer.strip()
        self.feedback = self.session.submit(text) if text else self.session.skip()
        self.answer = ""
        if self.session.finished:
            self.finished = True
        self._build_doc()
        if self.finished:
            self._jump_to(self.summary_start or 0)
        self.dirty = True

    # ------------------------------------------------------------ 鼠标

    def _click(self, x, y, double):
        row = y - self.origin[1] - self.HEADER_ROWS
        col = x - self.origin[0]
        index = self.top + row
        in_passage = (not self.too_small and 0 <= row < self.body_h
                      and 0 <= col < self.left_w and index < self.passage_n)
        if in_passage:
            super()._click(x, y, double)
        else:
            if not double:
                self._last_click = (0.0, x, y)
            self._clear_panel()

    def _wheel(self, step, x, y):
        if x - self.origin[0] >= self.left_w + self.gap_w + 1:
            return
        self._scroll(step)

    def _vt_mouse(self, button, x, y, kind):
        if kind == "m" or button & 32:
            return
        if button & 64:
            self._wheel(-3 if (button & 1) == 0 else 3, x, y)
            return
        if (button & 3) in (0, 2):
            self._click(x, y, double=False)

    def _handle_mouse(self, mouse):
        flags = mouse.dwEventFlags
        x, y = mouse.dwMousePosition.X, mouse.dwMousePosition.Y
        if flags & MOUSE_WHEELED:
            wheel = ctypes.c_short((mouse.dwButtonState >> 16) & 0xFFFF).value
            self._wheel(-3 if wheel > 0 else 3, x, y)
        elif flags & MOUSE_MOVED:
            return
        elif flags & DOUBLE_CLICK:
            self._click(x, y, double=True)
        elif flags == 0 and mouse.dwButtonState & (LEFT_BUTTON | RIGHT_BUTTON):
            self._click(x, y, double=False)

    # ------------------------------------------------------------ 绘制

    def _footer(self):
        if self.finished:
            text = (f"🏁 完成 {self.session.correct}/{self.session.answered} "
                    f"{self.session.score * 100:.0f}%   [回车/q 返回] 可滚动查看")
            return fit(text, self.width, S_YELLOW)
        s = self.session
        block = s.blocks[s.current] if s.blocks else None
        span = block["span"] if block else ""
        hint = "选字母后回车" if block and block["multi"] else "输入后回车提交"
        text = f"第 {span} 题 > {self.answer}"
        if self.feedback:
            text += f"   {self.feedback}"
        end = min(self.top + self.body_h, len(self.doc))
        text += (f"   [{hint}] 已答 {s.answered}/{s.total} 正确 {s.correct}"
                 f" | 行 {self.top + 1}-{end}/{len(self.doc)}")
        return fit(text, self.width, S_YELLOW)

    def _frame(self):
        if self.too_small:
            return [fit("窗口太小", self.width, S_CYAN),
                    fit("请放大终端后继续，Ctrl+C 退出", self.width, S_GREY)]
        rows = [
            fit(self.title, self.width, S_CYAN),
            fit("双击原文查词 | 滚轮/方向键滚动 | Tab 跳到当前题 | 输入答案回车提交 | Ctrl+C 退出",
                self.width, S_GREY),
        ]
        for i in range(self.body_h):
            index = self.top + i
            text, style = self.doc[index] if index < len(self.doc) else ("", "")
            left = self._render_doc_line(text, style, index)
            rows.append(left + " " * self.gap_w
                        + S_DIM + "│" + S_RESET + " " + self._render_panel(i))
        rows.append(self._footer())
        return rows

    def _render_doc_line(self, text, style, index):
        w = max(1, self.left_w)
        if index < self.passage_n and self.sel and self.sel[0] == index:
            line = self.lines[index]
            _, start, end = self.sel
            body = line[:start] + S_REV + line[start:end] + S_RESET + line[end:]
            return body + " " * max(0, w - disp_width(line))
        text = clip_display(text, w)
        pad = " " * max(0, w - disp_width(text))
        return f"{style}{text}{pad}{S_RESET}" if style else text + pad

    def _render_panel(self, row):
        w = max(1, self.panel_w - 2)
        text, style = ("", "")
        if row < len(self.panel):
            text, style = self.panel[row]
        text = clip_display(text, w)
        pad = " " * max(0, w - disp_width(text))
        body = f"{style}{text}{pad}{S_RESET}" if style else text + pad
        return body


def run_exam(title, body_text, session):
    dictionary = Dictionary()
    try:
        ExamReader(title or "雅思阅读", body_text, dictionary, session).run()
    finally:
        dictionary.close()
