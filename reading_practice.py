#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雅思阅读训练：选套题、读原文、逐题作答、判分与错题记录。"""

import json
import math
import random
import re
import shutil
import sqlite3
import textwrap
from datetime import datetime

C_RESET = "\033[0m"
C_TITLE = "\033[1;36m"
C_HEAD = "\033[1;33m"
C_OK = "\033[1;32m"
C_ERR = "\033[1;31m"
C_DIM = "\033[2m"
C_NOTE = "\033[1;35m"

WIDTH = 78

KIND_NAMES = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "true_false_not_given": "判断题 TRUE / FALSE / NOT GIVEN",
    "yes_no_not_given": "判断题 YES / NO / NOT GIVEN",
    "matching": "匹配题",
    "classification": "分类题",
    "summary_completion": "摘要填空",
    "sentence_completion": "句子填空",
    "table_completion": "表格填空",
    "diagram_completion": "图示填空",
    "short_answer": "简答题",
}

FREQ_NAMES = {"high": "高频", "medium": "中频", "low": "低频"}


def wrap_text(text, width=WIDTH, indent="  "):
    lines = []
    for para in str(text or "").split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(para, width=max(20, width - len(indent))) or [""])
    return [indent + ln if ln else "" for ln in lines]


def norm_answer(value):
    s = str(value or "").strip().lower()
    s = s.replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .,;:!?\"'")
    s = re.sub(r"^(the|a|an)\s+", "", s)
    return s


def loose_answer(value):
    return re.sub(r"[\s\-]", "", norm_answer(value))


class ReadingPractice:
    def __init__(self, db_path="word_study.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.ready = self._check_tables()

    def _check_tables(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reading_passage'"
        ).fetchone()
        if not row:
            print("\n⚠️ 还没有雅思阅读题库。")
            print("   请先运行: python import_reading.py")
            return False
        if not self.conn.execute("SELECT 1 FROM reading_passage LIMIT 1").fetchone():
            print("\n⚠️ 阅读题库为空，请先运行: python import_reading.py")
            return False
        return True

    # ------------------------------------------------------------ 菜单

    def run(self):
        if not self.ready:
            return
        while True:
            print("\n" + "=" * 60)
            print("📖 雅思阅读训练")
            print("=" * 60)
            passages, questions, attempts, wrongs = self._counts()
            print(f"题库: {passages} 篇 / {questions} 题 | 已练 {attempts} 套 | 错题 {wrongs} 道")
            print("-" * 60)
            print("1. 📚 按 P1 / P2 / P3 选套题")
            print("2. 🎲 随机一套")
            print("3. 🔥 高频优先随机")
            print("4. 🩹 错题重练")
            print("5. 📊 练习记录")
            print("0. 返回主菜单")
            print("=" * 60)
            choice = input("请选择 (0-5): ").strip()
            try:
                if choice == "0":
                    break
                elif choice == "1":
                    self._choose_category()
                elif choice == "2":
                    self._practice_random()
                elif choice == "3":
                    self._practice_random("high")
                elif choice == "4":
                    self._wrong_menu()
                elif choice == "5":
                    self._show_stats()
                else:
                    print("❌ 无效选择")
            except KeyboardInterrupt:
                print("\n↩️ 已返回阅读菜单")

    def _counts(self):
        c = self.conn
        passages = c.execute("SELECT COUNT(*) FROM reading_passage").fetchone()[0]
        questions = c.execute("SELECT COUNT(*) FROM reading_question").fetchone()[0]
        attempts = c.execute("SELECT COUNT(*) FROM reading_attempt").fetchone()[0]
        wrongs = c.execute("SELECT COUNT(*) FROM reading_wrong").fetchone()[0]
        return passages, questions, attempts, wrongs

    def _choose_category(self):
        while True:
            print("\n" + "=" * 60)
            print("📚 选择套题类型")
            print("=" * 60)
            print("1. P1 (Passage 1, 较易)")
            print("2. P2 (Passage 2, 中等)")
            print("3. P3 (Passage 3, 较难)")
            print("0. 返回")
            print("=" * 60)
            choice = input("请选择 (0-3): ").strip()
            if choice == "0":
                return
            if choice in ("1", "2", "3"):
                self._list_passages(f"P{choice}")

    def _list_passages(self, category):
        rows = self.conn.execute(
            """
            SELECT p.*,
                   (SELECT MAX(score) FROM reading_attempt a WHERE a.passage_id = p.id) AS best,
                   (SELECT COUNT(*) FROM reading_attempt a WHERE a.passage_id = p.id) AS tries
            FROM reading_passage p
            WHERE p.category = ?
            ORDER BY CASE p.frequency WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, p.title
            """,
            (category,),
        ).fetchall()
        if not rows:
            print(f"❌ 没有 {category} 套题")
            return
        page, per = 0, 12
        pages = math.ceil(len(rows) / per)
        while True:
            print("\n" + "=" * 60)
            print(f"📚 {category} 套题（共 {len(rows)} 篇）  第 {page + 1}/{pages} 页")
            print("=" * 60)
            start = page * per
            for i, r in enumerate(rows[start:start + per], start=start + 1):
                freq = FREQ_NAMES.get(r["frequency"], r["frequency"])
                best = f"{r['best'] * 100:.0f}%" if r["best"] is not None else "--"
                title = r["title"]
                if len(title) > 42:
                    title = title[:40] + "…"
                print(f"{i:>3}. [{freq}] {title}  ({r['total_questions']}题, 最佳 {best})")
            print("-" * 60)
            print(f"输入编号开始练习 | n 下一页 | p 上一页 | 0 返回")
            choice = input(">>> ").strip().lower()
            if choice == "0":
                return
            if choice == "n":
                page = min(page + 1, pages - 1)
                continue
            if choice == "p":
                page = max(page - 1, 0)
                continue
            if not choice.isdigit():
                print("❌ 无效输入")
                continue
            index = int(choice) - 1
            if 0 <= index < len(rows):
                self._practice_passage(rows[index]["id"])
            else:
                print("❌ 编号超出范围")

    def _practice_random(self, frequency=None):
        sql = "SELECT id, title FROM reading_passage"
        params = ()
        if frequency:
            sql += " WHERE frequency = ?"
            params = (frequency,)
        sql += " ORDER BY RANDOM() LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        if not row:
            print("❌ 题库为空")
            return
        print(f"\n🎲 抽到: {row['title']}")
        self._practice_passage(row["id"])

    # ------------------------------------------------------------ 练习

    def _practice_passage(self, passage_id, only_question_ids=None):
        p = self.conn.execute(
            "SELECT * FROM reading_passage WHERE id = ?", (passage_id,)
        ).fetchone()
        if not p:
            print("❌ 找不到这篇阅读")
            return
        groups = self.conn.execute(
            "SELECT * FROM reading_group WHERE passage_id = ? ORDER BY seq", (passage_id,)
        ).fetchall()
        plan = []
        for g in groups:
            qs = self.conn.execute(
                "SELECT * FROM reading_question WHERE group_id = ? "
                "ORDER BY CAST(display_number AS INTEGER), qid",
                (g["id"],),
            ).fetchall()
            if only_question_ids is not None:
                qs = [q for q in qs if q["id"] in only_question_ids]
            if qs:
                plan.append((g, qs))
        if not plan:
            print("❌ 这篇没有可练的题目")
            return
        total = sum(len(qs) for _, qs in plan)

        print("\n" + "=" * 60)
        print(f"📄 {p['title']}")
        print(f"类型: {p['category']} · {FREQ_NAMES.get(p['frequency'], p['frequency'])} | "
              f"本次 {total} 题 | 来源: GPL-3.0 公开题库")
        print("=" * 60)
        if only_question_ids is not None:
            print("🩹 错题重练模式：只考错过的题")
        width = max(40, shutil.get_terminal_size((100, 30)).columns)
        session = ExamSession(self, p, plan)
        try:
            from reading_exam import run_exam
        except Exception:
            run_exam = None
        if run_exam is not None:
            try:
                run_exam(p["title"], p["body_text"], session)
            except RuntimeError:
                session.abort()
                if session.answered:
                    session.report()
                    return
                print("⚠️ 无法打开全屏答题界面，改用普通模式")
            else:
                session.report()
                return
        self._print_passage(p["body_text"], max(40, width // 2))
        self._print_exam(plan, width)
        self._answer_session(p, plan, total)

    def _make_exam_block(self, group, questions):
        kind = group["kind"]
        multi = kind == "multi_choice" and len(questions) > 1
        per_question, group_options = self._parse_layout_options(group["layout"])
        items = []
        for q in questions:
            opts = json.loads(q["options_json"] or "[]")
            labels = [str(o.get("label") or o.get("value") or "") for o in opts]
            if labels and all(len(label) <= 2 for label in labels):
                texts = per_question.get(str(q["display_number"]))
                if texts and len(texts) >= 2:
                    labels = [f"{k}. {v}" for k, v in texts]
                elif group_options:
                    labels = [f"{k}. {v}" for k, v in group_options]
            items.append({
                "number": str(q["display_number"]),
                "prompt": str(q["prompt"] or "").strip(),
                "options": labels,
            })
        if multi:
            block_options = ([f"{k}. {v}" for k, v in group_options]
                             if group_options else (items[0]["options"] if items else []))
            per_item = False
        elif items and all(item["options"] == items[0]["options"] for item in items):
            block_options = items[0]["options"]
            per_item = False
        else:
            block_options = []
            per_item = True
        return {
            "kind": kind,
            "kind_label": KIND_NAMES.get(kind, kind),
            "span": self._number_span([item["number"] for item in items]),
            "instruction": str(group["instruction"] or "").strip(),
            "multi": multi,
            "questions": questions,
            "items": items,
            "block_options": block_options,
            "per_item_options": per_item,
            "state": None,
            "user": "",
            "expected": [],
        }

    def _print_passage(self, body_text, width):
        print(f"\n{C_TITLE}📖 原文{C_RESET}")
        print("-" * 60)
        for line in wrap_text(body_text or "（原文为空）", width=width, indent=""):
            print(line)

    def _print_exam(self, plan, width):
        total = sum(len(qs) for _, qs in plan)
        print(f"\n{C_TITLE}📝 题目（共 {total} 题）{C_RESET}")
        print("-" * 60)
        for group, questions in plan:
            span = self._number_span([str(q["display_number"]) for q in questions])
            print(f"\n{C_HEAD}{KIND_NAMES.get(group['kind'], group['kind'])}{C_RESET}  "
                  f"{C_DIM}第 {span} 题{C_RESET}")
            instruction = (group["instruction"] or "").strip()
            if instruction:
                for line in wrap_text(instruction, width=width):
                    if line.strip():
                        print(f"{C_DIM}{line}{C_RESET}")
            instr_flat = self._flat_text(instruction)
            per_question, group_options = self._parse_layout_options(group["layout"])
            displays = []
            for q in questions:
                opts = json.loads(q["options_json"] or "[]")
                bare = bool(opts) and all(
                    len(str(o.get("label") or o.get("value") or "")) <= 2 for o in opts)
                texts = per_question.get(str(q["display_number"])) if bare else None
                if texts and len(texts) >= 2:
                    opts = [{"label": f"{k}. {v}"} for k, v in texts]
                displays.append(opts)
            shared = bool(displays[0]) and all(d == displays[0] for d in displays)
            is_multi_block = group["kind"] == "multi_choice" and len(questions) > 1
            if shared and not is_multi_block and not self._options_reflected(displays[0], instr_flat):
                print("选项:")
                for line in self._option_lines(displays[0], width):
                    print(line)

            if is_multi_block:
                print(f"\n{C_TITLE}第 {span} 题（多选，从选项中选 {len(questions)} 个）{C_RESET}")
                prompt = str(questions[0]["prompt"] or "").strip()
                if self._flat_text(prompt) not in instr_flat:
                    for line in wrap_text(prompt, width=width):
                        print(line)
                block_options = ([{"label": f"{k}. {v}"} for k, v in group_options]
                                 if group_options else displays[0])
                if block_options and not self._options_reflected(block_options, instr_flat):
                    print("选项:")
                    for line in self._option_lines(block_options, width):
                        print(line)
                continue

            for q, q_options in zip(questions, displays):
                prompt = str(q["prompt"] or "").strip()
                if "…" in prompt:
                    m = re.match(r"^((?:Paragraph|Section)\s+[A-Z])\b", prompt)
                    if m:
                        prompt = m.group(1)
                show_prompt = bool(prompt) and self._flat_text(prompt) not in instr_flat
                show_options = (bool(q_options) and not shared
                                and not self._options_reflected(q_options, instr_flat))
                if not show_prompt and not show_options:
                    continue
                print(f"\n{C_TITLE}第 {q['display_number']} 题{C_RESET}")
                if show_prompt:
                    for line in wrap_text(prompt, width=width):
                        print(line)
                    if q["kind"] == "diagram_completion":
                        print(f"{C_DIM}（图示题，请对照原文示意图）{C_RESET}")
                if show_options:
                    print("选项:")
                    for line in self._option_lines(q_options, width):
                        print(line)
        print("\n" + "-" * 60)

    @staticmethod
    def _parse_layout_options(layout):
        """选项正文缺失时，从 layout 里按题号还原每题的选项。"""
        per_question = {}
        group_level = []
        current = None
        for raw in str(layout or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            m = re.match(r"^(\d{1,3})[.)]?\s+\S", line)
            if m:
                current = m.group(1)
                per_question.setdefault(current, [])
                continue
            if re.fullmatch(r"[A-Z]", line):
                continue
            m = re.match(r"^([A-Z])[.)、]\s+(\S.*)$", line)
            if m:
                entry = (m.group(1), m.group(2).strip())
                if current:
                    per_question[current].append(entry)
                else:
                    group_level.append(entry)
                continue
            m = re.match(r"^([A-Z])\s+(\S.*)$", line)
            if m and current:
                per_question[current].append((m.group(1), m.group(2).strip()))
        return per_question, group_level

    @staticmethod
    def _flat_text(text):
        return re.sub(r"\s+", " ", str(text or "").replace("_", " ")).strip().lower()

    @classmethod
    def _options_reflected(cls, options, instr_flat):
        """说明文字里已完整写出选项时，正文不再重复打印。"""
        if not options:
            return False
        for opt in options:
            key = cls._flat_text(opt.get("label") or opt.get("value") or "")
            key = re.sub(r"^[a-z][.)、]\s*", "", key).rstrip(".")
            if not key:
                return False
            if len(key) <= 2:
                if not re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", instr_flat):
                    return False
            elif key not in instr_flat:
                return False
        return True

    @staticmethod
    def _number_span(numbers):
        if len(numbers) == 1:
            return numbers[0]
        if (all(n.isdigit() for n in numbers)
                and int(numbers[-1]) - int(numbers[0]) == len(numbers) - 1):
            return f"{numbers[0]}-{numbers[-1]}"
        return "、".join(numbers)

    def _answer_session(self, passage, plan, total):
        started = datetime.now().isoformat(timespec="seconds")
        attempt_id = self.conn.execute(
            "INSERT INTO reading_attempt (passage_id, started_at, total) VALUES (?, ?, 0)",
            (passage["id"], started),
        ).lastrowid
        self.conn.commit()

        answered = 0
        correct = 0
        wrong_items = []
        quit_session = False
        index = 0

        print(f"\n{C_HEAD}✍️ 开始作答（q 退出，直接回车跳过）{C_RESET}")
        for group, questions in plan:
            if quit_session:
                break
            span = self._number_span([str(q["display_number"]) for q in questions])

            if group["kind"] == "multi_choice" and len(questions) > 1:
                index += len(questions)
                try:
                    raw = input(
                        f"{C_HEAD}[{span}] 选择 {len(questions)} 个字母 "
                        f"(空格分隔, 回车跳过, q 退出): {C_RESET}"
                    )
                except EOFError:
                    quit_session = True
                    break
                answer = raw.strip()
                if answer.lower() == "q":
                    quit_session = True
                    break
                letters = [x.upper() for x in re.findall(r"[A-Za-z]+", answer)]
                if not letters:
                    print(f"{C_DIM}  已跳过（不计分）{C_RESET}")
                    continue
                picked = {norm_answer(x) for x in letters}
                expected_all = []
                graded = 0
                block_wrong = False
                for q in questions:
                    expected = json.loads(q["answers_json"] or "[]")
                    if not expected:
                        continue
                    graded += 1
                    answered += 1
                    ok = any(norm_answer(e) in picked for e in expected)
                    self.conn.execute(
                        "INSERT INTO reading_answer (attempt_id, question_id, user_answer, is_correct, answered_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (attempt_id, q["id"], " ".join(letters), 1 if ok else 0,
                         datetime.now().isoformat(timespec="seconds")),
                    )
                    if ok:
                        correct += 1
                        self.conn.execute("DELETE FROM reading_wrong WHERE question_id = ?", (q["id"],))
                    else:
                        block_wrong = True
                        expected_all.extend(expected)
                        self._record_wrong(q, answer)
                if block_wrong:
                    wrong_items.append({
                        "num": f"{questions[0]['display_number']}-{questions[-1]['display_number']}",
                        "prompt": questions[0]["prompt"],
                        "answer": " ".join(letters),
                        "answers": expected_all,
                    })
                    print(f"{C_ERR}  ❌ 错误，正确答案: {' / '.join(expected_all)}{C_RESET}")
                elif graded:
                    print(f"{C_OK}  ✅ 正确{C_RESET}")
                else:
                    print(f"{C_NOTE}  本题缺少标准答案，跳过判分{C_RESET}")
                self.conn.commit()
                continue

            for q in questions:
                index += 1
                try:
                    raw = input(f"{C_HEAD}[{index}/{total}] 第 {q['display_number']} 题 > {C_RESET}")
                except EOFError:
                    quit_session = True
                    break
                answer = raw.strip()
                if answer.lower() == "q":
                    quit_session = True
                    break
                if not answer:
                    print(f"{C_DIM}  已跳过（不计分）{C_RESET}")
                    continue

                answer = self._canonical(answer, q["kind"])
                answers = json.loads(q["answers_json"] or "[]")
                if not answers:
                    print(f"{C_NOTE}  本题缺少标准答案，跳过判分{C_RESET}")
                    continue
                answered += 1
                ok = self._is_correct(answer, answers)
                self.conn.execute(
                    "INSERT INTO reading_answer (attempt_id, question_id, user_answer, is_correct, answered_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (attempt_id, q["id"], answer, 1 if ok else 0,
                     datetime.now().isoformat(timespec="seconds")),
                )
                if ok:
                    correct += 1
                    self.conn.execute("DELETE FROM reading_wrong WHERE question_id = ?", (q["id"],))
                    print(f"{C_OK}  ✅ 正确{C_RESET}")
                else:
                    wrong_items.append({
                        "num": q["display_number"],
                        "prompt": q["prompt"],
                        "answer": answer,
                        "answers": answers,
                    })
                    self._record_wrong(q, answer)
                    print(f"{C_ERR}  ❌ 错误，正确答案: {' / '.join(answers)}{C_RESET}")
                self.conn.commit()

        finished = datetime.now().isoformat(timespec="seconds")
        score = correct / answered if answered else 0.0
        self.conn.execute(
            "UPDATE reading_attempt SET finished_at = ?, total = ?, correct = ?, score = ? WHERE id = ?",
            (finished, answered, correct, score, attempt_id),
        )
        if answered == 0:
            self.conn.execute("DELETE FROM reading_attempt WHERE id = ?", (attempt_id,))
        self.conn.commit()

        print("\n" + "=" * 60)
        if answered:
            print(f"🏁 本次成绩: {correct}/{answered}  正确率 {score * 100:.0f}%")
        else:
            print("🏁 本次没有作答")
        if wrong_items:
            print(f"\n❌ 错题回顾（{len(wrong_items)} 题）:")
            for item in wrong_items:
                print(f"\n  第 {item['num']} 题:")
                for line in wrap_text(item["prompt"], width=WIDTH, indent="    "):
                    print(line)
                print(f"    你的答案: {item['answer'] or '(空)'}    "
                      f"正确答案: {' / '.join(item['answers'])}")
            print(f"\n{C_NOTE}💡 这些题已加入错题本，可从“错题重练”再次练习{C_RESET}")
        else:
            print("🎉 全部答对！")
        print("=" * 60)
        input("回车返回...")

    @staticmethod
    def _option_lines(options, width):
        labels = [str(opt.get("label") or opt.get("value") or "") for opt in options]
        if labels and all(len(label) <= 2 for label in labels):
            return ["      " + " ".join(labels)]
        lines = []
        for label in labels:
            lines.extend(wrap_text(label, width=width, indent="      "))
        return lines

    @staticmethod
    def _canonical(text, kind):
        t = text.strip()
        low = t.lower()
        if kind == "true_false_not_given":
            mapping = {"t": "TRUE", "true": "TRUE", "f": "FALSE", "false": "FALSE",
                       "n": "NOT GIVEN", "ng": "NOT GIVEN", "not given": "NOT GIVEN"}
            return mapping.get(low, t)
        if kind == "yes_no_not_given":
            mapping = {"y": "YES", "yes": "YES", "n": "NO", "no": "NO",
                       "ng": "NOT GIVEN", "not given": "NOT GIVEN"}
            return mapping.get(low, t)
        return t

    def _is_correct(self, answer, answers):
        user = norm_answer(answer)
        loose = loose_answer(answer)
        for expected in answers:
            if user == norm_answer(expected) or loose == loose_answer(expected):
                return True
        return False

    def _record_wrong(self, question, answer):
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO reading_wrong (question_id, passage_id, wrong_count, last_user_answer, last_wrong_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(question_id) DO UPDATE SET
                wrong_count = wrong_count + 1,
                last_user_answer = excluded.last_user_answer,
                last_wrong_at = excluded.last_wrong_at
            """,
            (question["id"], question["passage_id"], answer, now),
        )

    # ------------------------------------------------------------ 错题与统计

    def _wrong_menu(self):
        rows = self.conn.execute(
            """
            SELECT w.passage_id, p.title, p.category, COUNT(*) AS n, SUM(w.wrong_count) AS times
            FROM reading_wrong w JOIN reading_passage p ON p.id = w.passage_id
            GROUP BY w.passage_id ORDER BY times DESC
            """
        ).fetchall()
        if not rows:
            print("\n🎉 错题本是空的")
            return
        while True:
            print("\n" + "=" * 60)
            print(f"🩹 错题本（{len(rows)} 篇）")
            print("=" * 60)
            for i, r in enumerate(rows, start=1):
                title = r["title"] if len(r["title"]) <= 42 else r["title"][:40] + "…"
                print(f"{i:>3}. [{r['category']}] {title}  ({r['n']}道, 累计错{r['times']}次)")
            print("-" * 60)
            print("输入编号重练该篇错题 | 0 返回")
            choice = input(">>> ").strip()
            if choice == "0":
                return
            if not choice.isdigit():
                print("❌ 无效输入")
                continue
            idx = int(choice) - 1
            if 0 <= idx < len(rows):
                pid = rows[idx]["passage_id"]
                ids = {r["question_id"] for r in self.conn.execute(
                    "SELECT question_id FROM reading_wrong WHERE passage_id = ?", (pid,))}
                self._practice_passage(pid, ids)
                return
            print("❌ 编号超出范围")

    def _show_stats(self):
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(correct), 0) AS c, "
            "COALESCE(SUM(total), 0) AS t, COALESCE(AVG(score), 0) AS avg FROM reading_attempt"
        ).fetchone()
        print("\n" + "=" * 60)
        print("📊 阅读练习记录")
        print("=" * 60)
        print(f"累计完成 {row['n']} 套 | 答题 {row['t']} 道 | 正确 {row['c']} 道 | "
              f"平均正确率 {row['avg'] * 100:.0f}%")
        recent = self.conn.execute(
            """
            SELECT a.*, p.title FROM reading_attempt a
            JOIN reading_passage p ON p.id = a.passage_id
            ORDER BY a.id DESC LIMIT 10
            """
        ).fetchall()
        if recent:
            print("\n最近练习:")
            for r in recent:
                title = r["title"] if len(r["title"]) <= 36 else r["title"][:34] + "…"
                when = (r["finished_at"] or r["started_at"] or "")[:16].replace("T", " ")
                print(f"  {when}  {title}  {r['correct']}/{r['total']} ({r['score'] * 100:.0f}%)")
        top = self.conn.execute(
            """
            SELECT q.prompt, w.wrong_count, p.title
            FROM reading_wrong w
            JOIN reading_question q ON q.id = w.question_id
            JOIN reading_passage p ON p.id = w.passage_id
            ORDER BY w.wrong_count DESC, w.last_wrong_at DESC LIMIT 8
            """
        ).fetchall()
        if top:
            print("\n最常错的题:")
            for r in top:
                prompt = r["prompt"] if len(r["prompt"]) <= 60 else r["prompt"][:58] + "…"
                print(f"  ×{r['wrong_count']}  {prompt}  ({r['title'][:24]})")
        else:
            print("\n🎉 错题本是空的")
        input("\n回车返回...")


class ExamSession:
    """全屏答题会话：题目分块、判分、写库与成绩汇总。"""

    def __init__(self, practice, passage, plan):
        self.p = practice
        self.passage = passage
        self.blocks = []
        for group, questions in plan:
            if group["kind"] == "multi_choice" and len(questions) > 1:
                self.blocks.append(practice._make_exam_block(group, questions))
            else:
                for question in questions:
                    self.blocks.append(practice._make_exam_block(group, [question]))
        self.total = sum(len(block["questions"]) for block in self.blocks)
        self.current = 0
        self.attempt_id = None
        self.answered = 0
        self.correct = 0
        self.wrong_items = []
        self.finished = False
        self.finalized = False

    @property
    def score(self):
        return self.correct / self.answered if self.answered else 0.0

    def _ensure_attempt(self):
        if self.attempt_id is None:
            self.attempt_id = self.p.conn.execute(
                "INSERT INTO reading_attempt (passage_id, started_at, total) VALUES (?, ?, 0)",
                (self.passage["id"], datetime.now().isoformat(timespec="seconds")),
            ).lastrowid
            self.p.conn.commit()

    def _insert_answer(self, question, answer, ok):
        self.p.conn.execute(
            "INSERT INTO reading_answer (attempt_id, question_id, user_answer, is_correct, answered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.attempt_id, question["id"], answer, 1 if ok else 0,
             datetime.now().isoformat(timespec="seconds")),
        )

    def _erase_wrong(self, question):
        self.p.conn.execute("DELETE FROM reading_wrong WHERE question_id = ?", (question["id"],))

    def submit(self, text):
        if self.finished:
            return ""
        block = self.blocks[self.current]
        self._ensure_attempt()
        feedback = (self._submit_multi(block, text) if block["multi"]
                    else self._submit_single(block, text))
        self.p.conn.commit()
        self._advance()
        return feedback

    def _submit_single(self, block, text):
        question = block["questions"][0]
        answer = self.p._canonical(text, question["kind"])
        answers = json.loads(question["answers_json"] or "[]")
        block["user"] = answer
        block["expected"] = answers
        if not answers:
            block["state"] = "skip"
            return "本题缺少标准答案，已跳过"
        self.answered += 1
        ok = self.p._is_correct(answer, answers)
        self._insert_answer(question, answer, ok)
        if ok:
            self.correct += 1
            self._erase_wrong(question)
            block["state"] = "ok"
            return "✅ 正确"
        block["state"] = "bad"
        self.wrong_items.append({
            "num": question["display_number"],
            "prompt": question["prompt"],
            "answer": answer,
            "answers": answers,
        })
        self.p._record_wrong(question, answer)
        return f"❌ 错误，正确答案: {' / '.join(answers)}"

    def _submit_multi(self, block, text):
        letters = [x.upper() for x in re.findall(r"[A-Za-z]+", text)]
        picked = {norm_answer(x) for x in letters}
        block["user"] = " ".join(letters)
        expected_all = []
        graded = 0
        block_wrong = False
        for question in block["questions"]:
            expected = json.loads(question["answers_json"] or "[]")
            if not expected:
                continue
            graded += 1
            self.answered += 1
            ok = any(norm_answer(e) in picked for e in expected)
            self._insert_answer(question, " ".join(letters), ok)
            if ok:
                self.correct += 1
                self._erase_wrong(question)
            else:
                block_wrong = True
                expected_all.extend(expected)
                self.p._record_wrong(question, text)
        block["expected"] = expected_all
        if not graded:
            block["state"] = "skip"
            return "本题缺少标准答案，已跳过"
        if not block_wrong:
            block["state"] = "ok"
            return "✅ 正确"
        block["state"] = "bad"
        self.wrong_items.append({
            "num": (f"{block['questions'][0]['display_number']}-"
                    f"{block['questions'][-1]['display_number']}"),
            "prompt": block["questions"][0]["prompt"],
            "answer": " ".join(letters),
            "answers": expected_all,
        })
        return f"❌ 错误，正确答案: {' / '.join(expected_all)}"

    def skip(self):
        if self.finished:
            return ""
        self.blocks[self.current]["state"] = "skip"
        self._advance()
        return "已跳过（不计分）"

    def _advance(self):
        self.current += 1
        if self.current >= len(self.blocks):
            self.current = len(self.blocks) - 1
            self._finalize()
            self.finished = True

    def _finalize(self):
        if self.finalized:
            return
        self.finalized = True
        if self.attempt_id is None:
            return
        self.p.conn.execute(
            "UPDATE reading_attempt SET finished_at = ?, total = ?, correct = ?, score = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), self.answered, self.correct,
             self.score, self.attempt_id),
        )
        if self.answered == 0:
            self.p.conn.execute("DELETE FROM reading_attempt WHERE id = ?", (self.attempt_id,))
        self.p.conn.commit()

    def abort(self):
        self._finalize()

    def report(self):
        print("\n" + "=" * 60)
        if self.answered:
            print(f"🏁 本次成绩: {self.correct}/{self.answered}  正确率 {self.score * 100:.0f}%")
        else:
            print("🏁 本次没有作答")
        if self.wrong_items:
            print(f"\n❌ 错题回顾（{len(self.wrong_items)} 题）:")
            for item in self.wrong_items:
                print(f"\n  第 {item['num']} 题:")
                for line in wrap_text(item["prompt"], width=WIDTH, indent="    "):
                    print(line)
                print(f"    你的答案: {item['answer'] or '(空)'}    "
                      f"正确答案: {' / '.join(item['answers'])}")
            print(f"\n{C_NOTE}💡 这些题已加入错题本，可从“错题重练”再次练习{C_RESET}")
        elif self.answered:
            print("🎉 全部答对！")
        print("=" * 60)
        input("回车返回...")


def main():
    ReadingPractice().run()


if __name__ == "__main__":
    main()
