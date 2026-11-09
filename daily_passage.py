#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""功能 8.6：每日单词短文。

优先使用今天新学和今天复习过的单词，先在本地雅思真题库里寻找
可改编的约 300 词素材；素材覆盖不足时，让 DeepSeek 原创一篇
雅思风格短文。每天只生成并缓存一篇，API Key 只保存在本地配置中。
"""

import getpass
import json
import os
import re
import shutil
import socket
import sqlite3
import textwrap
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime

C_RESET = "\033[0m"
C_TITLE = "\033[1;36m"
C_HEAD = "\033[1;33m"
C_OK = "\033[1;32m"
C_ERR = "\033[1;31m"
C_DIM = "\033[2m"
C_NOTE = "\033[1;35m"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_WORD_COUNT = 15
MIN_TARGET_WORDS = 8
MAX_TARGET_WORDS = 30
SOURCE_WINDOW = 150
PASSAGE_MIN_WORDS = 120
PASSAGE_MAX_WORDS = 150
PASSAGE_TARGET_WORDS = 135
PASSAGE_ACCEPT_MIN = 110
PASSAGE_ACCEPT_MAX = 160
MAX_UNKNOWN_WORDS = 2
BASIC_WORD_LIMIT = 3000
PROMPT_LEARNED_LIMIT = 300

_FUNCTION_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on",
    "at", "for", "with", "from", "by", "as", "is", "are", "was", "were",
    "be", "been", "being", "am", "do", "does", "did", "done", "have",
    "has", "had", "can", "could", "will", "would", "shall", "should",
    "may", "might", "must", "cannot", "not", "no", "yes", "this", "that",
    "these", "those", "it", "its", "i", "you", "he", "she", "we", "they",
    "me", "him", "us", "them", "my", "your", "his", "her", "our", "their",
    "who", "whom", "whose", "which", "what", "when", "where", "why", "how",
    "there", "here", "than", "then", "so", "too", "very", "just", "also",
    "only", "more", "most", "some", "any", "all", "both", "each", "every",
    "other", "another", "such", "own", "same", "up", "down", "out", "over",
    "under", "again", "once", "about", "into", "through", "during",
    "before", "after", "above", "below", "between", "among", "against",
    "because", "while", "although", "though", "unless", "until", "since",
    "whether", "however", "therefore", "thus", "per", "via",
}

_IRREGULAR_BASES = {
    "was": "be", "were": "be", "been": "be", "being": "be", "am": "be",
    "has": "have", "had": "have", "does": "do", "did": "do", "done": "do",
    "became": "become", "began": "begin", "begun": "begin", "broke": "break",
    "broken": "break", "brought": "bring", "built": "build", "bought": "buy",
    "caught": "catch", "chose": "choose", "chosen": "choose", "came": "come",
    "dealt": "deal", "drew": "draw", "drawn": "draw", "drove": "drive",
    "driven": "drive", "ate": "eat", "eaten": "eat", "fell": "fall",
    "fallen": "fall", "felt": "feel", "fought": "fight", "found": "find",
    "flew": "fly", "flown": "fly", "got": "get", "gotten": "get",
    "gave": "give", "given": "give", "went": "go", "gone": "go",
    "grew": "grow", "grown": "grow", "heard": "hear", "held": "hold",
    "kept": "keep", "knew": "know", "known": "know", "led": "lead",
    "left": "leave", "lost": "lose", "made": "make", "meant": "mean",
    "met": "meet", "paid": "pay", "ran": "run", "said": "say",
    "saw": "see", "seen": "see", "sold": "sell", "sent": "send",
    "sat": "sit", "slept": "sleep", "spoke": "speak", "spoken": "speak",
    "spent": "spend", "stood": "stand", "stole": "steal", "stolen": "steal",
    "swam": "swim", "swum": "swim", "took": "take", "taken": "take",
    "taught": "teach", "told": "tell", "thought": "think", "threw": "throw",
    "thrown": "throw", "understood": "understand", "woke": "wake",
    "woken": "wake", "won": "win", "wore": "wear", "worn": "wear",
    "wrote": "write", "written": "write",
}

SYSTEM_PROMPT = (
    "You are an experienced IELTS Academic Reading passage writer and editor. "
    "You write clear, natural English for Chinese learners. "
    "You always return a single valid JSON object and nothing else."
)

_SPELLING_PAIRS = (
    ("fulfill", "fulfil"),
    ("judgment", "judgement"),
    ("favour", "favor"),
    ("behaviour", "behavior"),
    ("colour", "color"),
    ("centre", "center"),
    ("organise", "organize"),
    ("realise", "realize"),
    ("analyse", "analyze"),
    ("defence", "defense"),
    ("offence", "offense"),
    ("licence", "license"),
    ("practise", "practice"),
    ("programme", "program"),
    ("travelling", "traveling"),
)


class DailyPassageError(Exception):
    """生成过程失败，可直接展示给用户。"""


def _clean_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"```[A-Za-z]*", "", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = text.replace("**", "")
    return text.strip()


def _clean_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")


def _sanitize_api_key(value):
    text = _ANSI_RE.sub("", str(value or ""))
    text = re.sub(r"^\s*bearer\s+", "", text, flags=re.IGNORECASE)
    text = "".join(
        ch for ch in text
        if 0x21 <= ord(ch) <= 0x7E and ch not in "`'\""
    )
    match = re.search(r"sk-[A-Za-z0-9_-]{10,}", text)
    return match.group(0) if match else text


def _word_count(text):
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", str(text or "")))


def _display_width(text):
    width = 0
    for ch in str(text or ""):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _console_width(default=72):
    try:
        columns = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        columns = 80
    return max(40, min(76, columns - 2 or default))


def _wrap_english(text, width=74, indent="  "):
    lines = []
    for para in str(text or "").split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        lines.extend(
            textwrap.wrap(para, width=max(20, width - _display_width(indent))) or [""]
        )
    return "\n".join(indent + line if line else "" for line in lines)


def _wrap_cjk(text, max_cols=56, indent="  "):
    lines = []
    for para in str(text or "").split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        line = indent
        width = _display_width(indent)
        for ch in para:
            ch_width = 0 if unicodedata.combining(ch) else (
                2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            )
            if width + ch_width > max_cols and line.strip():
                lines.append(line.rstrip())
                line = indent
                width = _display_width(indent)
            line += ch
            width += ch_width
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines)


def _patterns(word):
    base = str(word or "").strip().lower()
    if not base:
        return []
    variants = {base}
    for left, right in _SPELLING_PAIRS:
        if base == left:
            variants.add(right)
        elif base == right:
            variants.add(left)
    return [_pattern_for(v) for v in variants]


def _pattern_for(word):
    word = word.strip().lower()
    if " " in word:
        body = r"\s+".join(re.escape(part) for part in word.split())
        return re.compile(r"\b" + body + r"\b", re.IGNORECASE)
    if word.endswith("e"):
        body = re.escape(word[:-1]) + "e?"
    elif len(word) > 2 and word.endswith("y") and word[-2] not in "aeiou":
        stem = re.escape(word[:-1])
        body = "(?:" + stem + "y|" + stem + "i)"
    else:
        body = re.escape(word)
    return re.compile(
        r"\b" + body + r"(?:s|es|ed|d|ing|ly)?\b", re.IGNORECASE
    )


def _word_in_text(word, text):
    return any(pattern.search(text) for pattern in _patterns(word))


def _token_bases(token):
    word = str(token or "").strip("'-").lower().replace("’", "'")
    if not word:
        return set()
    bases = {word}
    if word in _IRREGULAR_BASES:
        bases.add(_IRREGULAR_BASES[word])
    if word.endswith("'s"):
        bases.add(word[:-2])
    if word.endswith("n't"):
        bases.add(word[:-3])
        if word in {"won't", "can't", "shan't"}:
            bases.add({"won't": "will", "can't": "can", "shan't": "shall"}[word])
    if word.endswith("ies"):
        bases.add(word[:-3] + "y")
    if word.endswith("ied"):
        bases.add(word[:-3] + "y")
    if word.endswith("es"):
        bases.add(word[:-2])
    if word.endswith("s") and not word.endswith("ss"):
        bases.add(word[:-1])
    for suffix in ("ed", "ing"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            stem = word[:-len(suffix)]
            bases.add(stem)
            bases.add(stem + "e")
            if len(stem) > 2 and stem[-1] == stem[-2]:
                bases.add(stem[:-1])
    if word.endswith("ly"):
        bases.add(word[:-2])
    if word.endswith("ily"):
        bases.add(word[:-3] + "y")
    return {base for base in bases if base}


def _sentence_for(word, text):
    for sentence in re.split(r"(?<=[.!?])\s+", _clean_space(text)):
        sentence = sentence.strip()
        if sentence and _word_in_text(word, sentence):
            return sentence
    return ""


def _short_meaning(raw, limit=100):
    text = re.split(r"【", str(raw or ""))[0]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class DailyPassage:
    def __init__(
        self,
        study_db_path="word_study.db",
        vocab_db_path=None,
        config_path=None,
    ):
        here = os.path.dirname(os.path.abspath(__file__))
        self.study_db_path = study_db_path
        self.vocab_db_path = vocab_db_path or os.path.join(here, "word_learn.db")
        self.config_path = config_path or os.path.join(here, "user_config.json")
        self.conn = sqlite3.connect(study_db_path)
        self.conn.row_factory = sqlite3.Row
        self._basic_cache = None
        self._ensure_table()

    # ------------------------------------------------------------ 数据表

    def _ensure_table(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_passage (
                local_date TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                topic TEXT,
                body TEXT NOT NULL,
                translation TEXT,
                source_type TEXT,
                source_id TEXT,
                source_title TEXT,
                source_excerpt TEXT,
                target_words TEXT,
                word_notes TEXT,
                model TEXT,
                created_at TEXT
            )
            """
        )
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(daily_passage)")
        }
        migrations = (
            ("topic", "TEXT"),
            ("source_excerpt", "TEXT"),
            ("word_notes", "TEXT"),
            ("model", "TEXT"),
        )
        for name, decl in migrations:
            if name not in columns:
                self.conn.execute(
                    f"ALTER TABLE daily_passage ADD COLUMN {name} {decl}"
                )
        self.conn.commit()

    def _today(self):
        return datetime.now().strftime("%Y%m%d")

    def _date_display(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _get_cached(self, local_date):
        try:
            return self.conn.execute(
                "SELECT * FROM daily_passage WHERE local_date = ?", (local_date,)
            ).fetchone()
        except sqlite3.Error:
            return None

    def _current_cached(self):
        row = self._get_cached(self._today())
        if row is None:
            return None
        try:
            body = row["body"]
        except (IndexError, KeyError, TypeError):
            return None
        count = _word_count(body)
        if PASSAGE_ACCEPT_MIN <= count <= PASSAGE_ACCEPT_MAX:
            return row
        return None

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------ 配置

    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_config(self, updates):
        config = self._load_config()
        config.update(updates)
        tmp_path = self.config_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception as exc:
            print(f"保存设置失败: {exc}")

    def _target_count(self):
        try:
            count = int(self._load_config().get("daily_passage_word_count",
                                              DEFAULT_WORD_COUNT))
        except (TypeError, ValueError):
            count = DEFAULT_WORD_COUNT
        return max(MIN_TARGET_WORDS, min(MAX_TARGET_WORDS, count))

    def _api_settings(self):
        config = self._load_config()
        base_url = (
            os.environ.get("DEEPSEEK_BASE_URL")
            or config.get("deepseek_base_url")
            or DEFAULT_BASE_URL
        )
        model = (
            os.environ.get("DEEPSEEK_MODEL")
            or config.get("deepseek_model")
            or DEFAULT_MODEL
        )
        return model, str(base_url).rstrip("/")

    def _resolve_api_key(self):
        env_key = _sanitize_api_key(os.environ.get("DEEPSEEK_API_KEY"))
        if env_key:
            return env_key, "环境变量 DEEPSEEK_API_KEY"
        key = _sanitize_api_key(self._load_config().get("deepseek_api_key"))
        if key:
            return key, "本地 user_config.json"
        return "", ""

    @staticmethod
    def _mask_key(key):
        if not key:
            return "未设置"
        if len(key) <= 8:
            return "*" * len(key)
        return f"{key[:4]}...{key[-4:]}"

    # ------------------------------------------------------------ 菜单

    def run(self):
        try:
            self._run_menu()
        except KeyboardInterrupt:
            print("\n已返回阅读菜单")

    def _run_menu(self):
        while True:
            cached = self._current_cached()
            targets = self.select_target_words(self._target_count())
            new_count = sum(1 for item in targets if item["tag"] == "新学")
            review_count = sum(1 for item in targets if item["tag"] == "今日复习")

            print("\n" + "=" * 60)
            print(f"{C_TITLE}每日单词短文（8.6）{C_RESET}  {self._date_display()}")
            print("=" * 60)
            print(
                f"本次目标词 {len(targets)} 个 "
                f"（新学 {new_count} / 今日复习 {review_count} / "
                f"巩固 {max(0, len(targets) - new_count - review_count)}）"
            )
            if cached:
                print(
                    f"今日短文：已生成《{cached['title']}》 "
                    f"{_word_count(cached['body'])} 词"
                )
            else:
                print("今日短文：尚未生成（每天生成一次，之后直接查看）")
            key, source = self._resolve_api_key()
            print(f"DeepSeek：{self._mask_key(key)}（{source or '未配置'}）")
            if key and len(key) != 35:
                print("提示：Key 长度异常，若生成失败请在菜单 4 重新粘贴")
            print("-" * 60)
            print("1. 生成/查看今日短文（分栏阅读，双击查词）")
            print("2. 重新生成今日短文（会再次调用 API）")
            print("3. 查看本次目标词")
            print("4. 设置 DeepSeek API Key")
            print(f"5. 设置目标词数量（当前 {self._target_count()}）")
            print("0. 返回")
            print("=" * 60)

            choice = input("请选择 (0-5): ").strip()
            if choice == "0":
                break
            if choice == "1":
                if cached:
                    self._display(cached)
                else:
                    self._generate_and_display(targets)
            elif choice == "2":
                if cached:
                    confirm = input("确认重新生成？会再次调用 DeepSeek API (y/N): ").strip()
                    if confirm.lower() != "y":
                        continue
                self._generate_and_display(targets, force=True)
            elif choice == "3":
                self._print_targets(targets)
            elif choice == "4":
                self._configure_key()
            elif choice == "5":
                self._configure_word_count()
            else:
                print("无效选择")

    # ------------------------------------------------------------ 选词

    def _usable_word(self, word):
        word = str(word or "").strip()
        if not word or len(word) < 2 or len(word) > 32:
            return False
        if len(word.split()) > 3:
            return False
        if not re.fullmatch(r"[A-Za-z][A-Za-z' -]*", word):
            return False
        return word.lower() not in {
            "a", "an", "the", "to", "of", "in", "on", "at", "and", "or", "is",
        }

    def select_target_words(self, limit):
        try:
            rows = self.conn.execute(
                """
                SELECT id, voc_id, vocabulary, first_study_date, add_date,
                       last_review_at, lapses, state, stability
                FROM study_list
                ORDER BY id
                """
            ).fetchall()
        except sqlite3.Error:
            return []
        if not rows:
            return []

        today = self._today()
        review_stats = {}
        try:
            for row in self.conn.execute(
                """
                SELECT voc_id, MIN(rating) AS min_rating,
                       MAX(reviewed_at) AS last_at
                FROM review_log
                WHERE local_date = ?
                GROUP BY voc_id
                """,
                (today,),
            ):
                review_stats[row["voc_id"]] = (
                    row["min_rating"] if row["min_rating"] is not None else 3,
                    row["last_at"] or 0,
                )
        except sqlite3.Error:
            pass

        chosen = []
        seen_ids = set()
        seen_words = set()

        def add(row, tag):
            if len(chosen) >= limit:
                return
            voc_id = row["voc_id"]
            word = str(row["vocabulary"] or "").strip()
            key = word.lower()
            if (not voc_id or voc_id in seen_ids or key in seen_words
                    or not self._usable_word(word)):
                return
            seen_ids.add(voc_id)
            seen_words.add(key)
            chosen.append({
                "voc_id": voc_id,
                "word": word,
                "meaning": "",
                "tag": tag,
            })

        new_rows = [
            row for row in rows
            if row["first_study_date"] == today or row["add_date"] == today
        ]
        for row in new_rows:
            add(row, "新学")

        reviewed_rows = [row for row in rows if row["voc_id"] in review_stats]
        reviewed_rows.sort(
            key=lambda row: (
                review_stats[row["voc_id"]][0],
                -review_stats[row["voc_id"]][1],
            )
        )
        for row in reviewed_rows:
            add(row, "今日复习")

        rest = [row for row in rows if row["voc_id"] not in seen_ids]
        rest.sort(
            key=lambda row: (
                -(row["lapses"] or 0),
                row["stability"] or 0,
                row["last_review_at"] or 0,
            )
        )
        for row in rest:
            add(row, "巩固")

        self._fill_meanings(chosen)
        return chosen

    def _fill_meanings(self, targets):
        if not targets or not os.path.exists(self.vocab_db_path):
            return
        try:
            vocab_conn = sqlite3.connect(self.vocab_db_path)
            try:
                ids = [item["voc_id"] for item in targets]
                placeholders = ",".join("?" for _ in ids)
                rows = vocab_conn.execute(
                    f"SELECT vc_id, vc_interpretation FROM VOC_TB "
                    f"WHERE vc_id IN ({placeholders})",
                    ids,
                ).fetchall()
                meanings = {row[0]: _short_meaning(row[1]) for row in rows}
            finally:
                vocab_conn.close()
        except sqlite3.Error:
            return
        for item in targets:
            item["meaning"] = meanings.get(item["voc_id"], "")

    def _print_targets(self, targets):
        print("\n" + "=" * 60)
        print("本次目标词")
        print("=" * 60)
        if not targets:
            print("学习列表为空，请先添加并学习单词。")
            print("=" * 60)
            return
        print("选词顺序：今日新学 → 今日复习 → 近期薄弱/巩固")
        print("-" * 60)
        for index, item in enumerate(targets, 1):
            meaning = f"  {item['meaning']}" if item["meaning"] else ""
            print(f"{index:>2}. [{item['tag']}] {item['word']}{meaning}")
        print("=" * 60)
        input("回车返回...")

    # ------------------------------------------------------------ 雅思真题素材

    def _clean_passage_body(self, body):
        text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(
            r"^READING PASSAGE\s+\d+.*?below\.\s*", "", text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _best_window(self, row, words):
        body = self._clean_passage_body(row["body_text"])
        if not body:
            return None
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", body)
            if part.strip()
        ]
        if not sentences:
            return None

        counts = [_word_count(sentence) for sentence in sentences]
        matches = [
            {word for word in words if _word_in_text(word, sentence)}
            for sentence in sentences
        ]
        freq_rank = {"high": 2, "medium": 1}.get(
            str(row["frequency"] or "").lower(), 0
        )

        best = None
        end = 0
        total_words = 0
        active = {}
        for start in range(len(sentences)):
            if end < start:
                end = start
                total_words = 0
                active = {}
            while end < len(sentences) and total_words < SOURCE_WINDOW:
                total_words += counts[end]
                for word in matches[end]:
                    active[word] = active.get(word, 0) + 1
                end += 1
            if total_words >= 120:
                score = (len(active), freq_rank, -abs(total_words - SOURCE_WINDOW))
                if best is None or score > best["score"]:
                    best = {
                        "score": score,
                        "id": row["id"],
                        "title": row["title"] or "",
                        "category": row["category"] or "",
                        "frequency": row["frequency"] or "",
                        "text": " ".join(sentences[start:end]),
                        "word_count": total_words,
                        "matched": sorted(active),
                    }
            total_words -= counts[start]
            for word in matches[start]:
                active[word] -= 1
                if active[word] <= 0:
                    del active[word]
        return best

    def find_ielts_source(self, targets):
        words = [item["word"] for item in targets]
        if not words:
            return None
        try:
            rows = self.conn.execute(
                """
                SELECT id, title, category, frequency, body_text
                FROM reading_passage
                """
            ).fetchall()
        except sqlite3.Error:
            return None
        if not rows:
            return None

        best = None
        for row in rows:
            window = self._best_window(row, words)
            if window is None:
                continue
            if best is None or window["score"] > best["score"]:
                best = window

        if not best:
            return None
        min_source = max(2, min(3, int(len(words) * 0.25 + 0.5)))
        if len(best["matched"]) < min_source:
            return None
        return best

    # ------------------------------------------------------------ AI

    def _require_key(self):
        key, _ = self._resolve_api_key()
        if key:
            return key
        print("\n尚未配置 DeepSeek API Key。")
        print("Key 只保存在本地 user_config.json，该文件已在 .gitignore 中，")
        print("不会提交到 GitHub；也可以使用环境变量 DEEPSEEK_API_KEY。")
        answer = input("是否现在设置？(y/N): ").strip().lower()
        if answer != "y":
            return ""
        self._configure_key()
        key, _ = self._resolve_api_key()
        return key

    def _configure_key(self):
        current, source = self._resolve_api_key()
        print("\n" + "=" * 60)
        print("设置 DeepSeek API Key")
        print("=" * 60)
        print(f"当前：{self._mask_key(current)}" + (f"（{source}）" if source else ""))
        print("输入 0 清除已保存的 Key；直接回车取消。")
        print("安全提示：Key 只写入本地 user_config.json，不会上传到 GitHub。")
        print("=" * 60)
        try:
            key = getpass.getpass("请输入 DeepSeek API Key: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已取消")
            return
        if not key:
            return
        if key == "0":
            self._save_config({"deepseek_api_key": ""})
            print("已清除本地保存的 Key")
            return
        key = _sanitize_api_key(key)
        if not key.startswith("sk-"):
            print("Key 格式不正确：应以 sk- 开头，且不要包含引号、空格或颜色控制字符")
            print("请从 platform.deepseek.com 重新复制完整 Key")
            return
        if len(key) < 20:
            print("Key 长度异常，未保存")
            return
        if len(key) != 35:
            print(f"提示：官方 Key 通常为 35 个字符，当前 {len(key)} 个，正在联网校验...")
        _, base_url = self._api_settings()
        valid, message = self._verify_key(key, base_url)
        if valid is False:
            print(f"Key 校验失败：{message}")
            print("未保存，请重新复制 Key 后再试")
            return
        self._save_config({"deepseek_api_key": key})
        print(f"已保存：{self._mask_key(key)}")
        if valid is None:
            print(message)
        if os.environ.get("DEEPSEEK_API_KEY"):
            print("注意：环境变量 DEEPSEEK_API_KEY 优先级更高，当前仍会使用环境变量。")

    def _verify_key(self, key, base_url):
        request = urllib.request.Request(
            base_url + "/models",
            headers={
                "Authorization": "Bearer " + key,
                "User-Agent": "WordStudy/8.6",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read(1)
            return True, "Key 验证通过"
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False, "Key 无效，请从 platform.deepseek.com 重新复制"
            if exc.code == 402:
                return False, "DeepSeek 账户余额不足"
            if exc.code == 403:
                return False, "Key 没有访问权限"
            return False, self._http_error_message(exc)
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            return None, "当前无法联网校验，已保存；生成时若失败请检查网络"
        except Exception:
            return None, "当前无法联网校验，已保存；生成时若失败请检查网络"

    def _configure_word_count(self):
        current = self._target_count()
        print("\n" + "=" * 60)
        print("设置目标词数量")
        print("=" * 60)
        print(f"当前：每篇短文使用 {current} 个目标词")
        print(f"范围：{MIN_TARGET_WORDS}-{MAX_TARGET_WORDS}；建议 8-15")
        print("词越多，短文越密；120-150 词正文建议 8-15 个。")
        print("=" * 60)
        value = input("请输入新的数量（直接回车取消）: ").strip()
        if not value:
            return
        try:
            count = int(value)
        except ValueError:
            print("请输入数字")
            return
        if not MIN_TARGET_WORDS <= count <= MAX_TARGET_WORDS:
            print(f"请输入 {MIN_TARGET_WORDS}-{MAX_TARGET_WORDS} 之间的数字")
            return
        self._save_config({"daily_passage_word_count": count})
        print(f"已设置为 {count} 个目标词")

    def _learned_words(self):
        try:
            rows = self.conn.execute(
                "SELECT vocabulary FROM study_list "
                "ORDER BY COALESCE(add_date, '') DESC, id DESC"
            ).fetchall()
        except sqlite3.Error:
            return []
        return [
            str(row[0]).strip().lower()
            for row in rows
            if row[0] and str(row[0]).strip()
        ]

    def _basic_words(self):
        if self._basic_cache is not None:
            return self._basic_cache
        words = set()
        try:
            conn = sqlite3.connect(
                f"file:{self.vocab_db_path}?mode=ro", uri=True
            )
            try:
                book = conn.execute(
                    "SELECT id FROM BK_TB WHERE name LIKE 'COCA%' "
                    "ORDER BY \"order\" LIMIT 1"
                ).fetchone()
                if book:
                    rows = conn.execute(
                        "SELECT v.vc_vocabulary FROM BK_VOC_TB bv "
                        "JOIN VOC_TB v ON v.vc_id = bv.voc_id "
                        "WHERE bv.book_id = ? ORDER BY bv.\"order\" LIMIT ?",
                        (book[0], BASIC_WORD_LIMIT),
                    ).fetchall()
                    words = {
                        str(row[0]).strip().lower()
                        for row in rows
                        if row[0] and str(row[0]).strip()
                    }
            finally:
                conn.close()
        except sqlite3.Error:
            words = set()
        words.update(_FUNCTION_WORDS)
        self._basic_cache = words
        return words

    def _allowed_vocabulary(self, target_words):
        allowed = set()
        for word in target_words:
            allowed.add(str(word).strip().lower())
            allowed.update(_token_bases(word))
        allowed.update(self._learned_words())
        allowed.update(self._basic_words())
        for left, right in _SPELLING_PAIRS:
            if left in allowed:
                allowed.add(right)
            if right in allowed:
                allowed.add(left)
        return allowed

    def _unknown_words(self, text, allowed):
        unknown = []
        seen = set()
        for token in re.findall(r"[A-Za-z][A-Za-z'’-]*", str(text or "")):
            key = token.lower().replace("’", "'")
            if key in seen:
                continue
            seen.add(key)
            if _token_bases(token) & allowed:
                continue
            if "-" in token:
                parts = [part for part in token.split("-") if part]
                if parts and all(_token_bases(part) & allowed for part in parts):
                    continue
            unknown.append(token)
        return unknown

    def _build_user_prompt(self, targets, source):
        learned = self._learned_words()[:PROMPT_LEARNED_LIMIT]
        known_line = ", ".join(learned) if learned else "none yet"
        lines = [
            "TASK: Write one IELTS Academic Reading Passage 1/2 style article.",
            f"TARGET LENGTH: {PASSAGE_MIN_WORDS}-{PASSAGE_MAX_WORDS} English "
            "words for the passage only.",
            f"TARGET WORDS ({len(targets)}):",
        ]
        for index, item in enumerate(targets, 1):
            meaning = f" - {item['meaning']}" if item["meaning"] else ""
            lines.append(f"{index}. {item['word']}{meaning}")

        lines.extend([
            "",
            "VOCABULARY LIMIT (very important):",
            "- Apart from the target words, use only the learner's known words "
            "below and very common everyday English (roughly the 3,000 most "
            "frequent words).",
            "- Do not use rare, academic, technical or idiomatic words. If an "
            "idea needs a difficult word, simplify the idea instead.",
            "- The learner's known words are ordered from newest to oldest; "
            "prefer the words near the top.",
            "LEARNER'S KNOWN WORDS:",
            known_line,
        ])

        if source:
            lines.extend([
                "",
                "SOURCE MATERIAL (an excerpt from a real IELTS passage; "
                "use its topic and key ideas):",
                f"Original title: {source['title']}",
                source["text"],
                "",
                "Stay close to the source's topic and line of argument, but "
                "rewrite the sentences for a new article. Do not copy more "
                "than 6 consecutive words from the source.",
            ])
        else:
            lines.extend([
                "",
                "SOURCE MATERIAL: none. Choose a clear IELTS-style topic such "
                "as education, health, environment, technology, work, urban "
                "life or culture.",
            ])

        lines.extend([
            "",
            "REQUIREMENTS:",
            "- Use every target word at least once. Inflections (plural, past "
            "tense, -ing) are allowed, but do not replace a target word with a "
            "synonym or a different part of speech.",
            "- The result must be one coherent article, not a list of sentences.",
            "- Keep every non-target word simple and common; do not explain words.",
            "- Do not include a glossary, the target-word list, bold markers, "
            "markdown, or Chinese inside the passage.",
            "- Give a natural Chinese translation of the whole passage.",
            "- For each target word, copy one exact sentence from your passage "
            "that contains it.",
            "- Return JSON only, with exactly these keys: title, topic, passage, "
            "translation, word_sentences.",
        ])
        return "\n".join(lines)

    def _call_deepseek(self, messages, key, model, base_url):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            # 推理模型的 reasoning_content 与 content 共用此预算，过小会导致正文为空
            "max_tokens": 8000,
        }
        request = urllib.request.Request(
            base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
                "User-Agent": "WordStudy/8.6",
            },
            method="POST",
        )

        last_error = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    raw = response.read()
                break
            except urllib.error.HTTPError as exc:
                message = self._http_error_message(exc)
                if exc.code in (400, 401, 402, 403, 429):
                    raise DailyPassageError(message) from exc
                last_error = DailyPassageError(message)
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, (socket.timeout, TimeoutError)):
                    message = "请求超时，请检查网络后重试"
                else:
                    message = "网络不可用，请检查网络或代理设置"
                last_error = DailyPassageError(message)
            except (socket.timeout, TimeoutError) as exc:
                last_error = DailyPassageError("请求超时，请检查网络后重试")
            if attempt == 0:
                print(f"  {last_error}，1 秒后重试...")
                time.sleep(1.0)

        if last_error is not None:
            raise last_error

        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") if isinstance(message, dict) else None
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            snippet = _clean_space(raw.decode("utf-8", "replace"))[:200]
            raise DailyPassageError(
                "DeepSeek 返回内容无法解析" + (f"：{snippet}" if snippet else "")
            ) from exc

        if not isinstance(content, str) or not content.strip():
            if finish == "length":
                raise DailyPassageError(
                    "DeepSeek 输出被截断：推理耗尽了 max_tokens 预算，正文为空；"
                    "请增大 max_tokens 后重试"
                )
            raise DailyPassageError("DeepSeek 返回的正文为空，请重试")
        return content

    def _http_error_message(self, exc):
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        detail = ""
        try:
            data = json.loads(body)
            detail = (data.get("error") or {}).get("message") or data.get("message")
        except Exception:
            detail = ""
        detail = _clean_space(detail)[:160]
        if exc.code == 401:
            return "API Key 无效或已过期，请在 8.6 菜单重新设置"
        if exc.code == 402:
            return "DeepSeek 账户余额不足"
        if exc.code == 403:
            return "API Key 没有访问权限"
        if exc.code == 429:
            return "请求过于频繁，请稍后再试"
        if exc.code == 400:
            if detail and detail.lower() != "bad request":
                return f"请求参数错误：{detail}"
            return (
                "请求参数错误：请检查 API Key 是否复制完整"
                "（不要带反引号、空格或终端颜色控制字符）"
            )
        if exc.code >= 500:
            return "DeepSeek 服务暂时不可用，请稍后再试"
        return f"DeepSeek 请求失败（HTTP {exc.code}）" + (
            f"：{detail}" if detail else ""
        )

    def _parse_json(self, content):
        text = str(content or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise DailyPassageError("DeepSeek 没有返回 JSON 内容")
        try:
            data = json.loads(text[start:end + 1])
        except ValueError as exc:
            raise DailyPassageError("DeepSeek 返回的 JSON 无法解析") from exc
        if not isinstance(data, dict):
            raise DailyPassageError("DeepSeek 返回的 JSON 结构不正确")
        return data

    def _normalize_result(self, data, targets):
        passage = _clean_text(
            data.get("passage") or data.get("body") or data.get("text")
        )
        if not passage:
            raise DailyPassageError("DeepSeek 返回的短文为空")
        title = _clean_text(data.get("title")) or "A Daily IELTS Passage"
        topic = _clean_text(data.get("topic"))
        translation = _clean_text(
            data.get("translation") or data.get("chinese") or ""
        )

        notes_raw = data.get("word_sentences") or {}
        notes = {}
        if isinstance(notes_raw, dict):
            for word, sentence in notes_raw.items():
                notes[str(word).strip().lower()] = _clean_space(sentence)
        elif isinstance(notes_raw, list):
            for item in notes_raw:
                if not isinstance(item, dict):
                    continue
                word = item.get("word") or item.get("target")
                sentence = item.get("sentence") or item.get("text")
                if word and sentence:
                    notes[str(word).strip().lower()] = _clean_space(sentence)

        for item in targets:
            key = item["word"].lower()
            sentence = notes.get(key) or ""
            if not sentence or not _word_in_text(item["word"], sentence):
                sentence = _sentence_for(item["word"], passage)
            notes[key] = sentence

        return {
            "title": title,
            "topic": topic,
            "passage": passage,
            "translation": translation,
            "word_sentences": notes,
        }

    def _score_result(self, result, targets):
        body = result["passage"]
        matched = []
        missing = []
        for item in targets:
            if _word_in_text(item["word"], body):
                matched.append(item["word"])
            else:
                missing.append(item["word"])
        count = _word_count(body)
        return (len(matched), -abs(count - PASSAGE_TARGET_WORDS)), missing, count

    def _generate_with_ai(self, targets, source, key):
        model, base_url = self._api_settings()
        allowed = self._allowed_vocabulary(
            [item["word"] for item in targets]
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_prompt(targets, source)},
        ]
        content = self._call_deepseek(messages, key, model, base_url)
        result = self._normalize_result(self._parse_json(content), targets)
        _, missing, count = self._score_result(result, targets)
        unknown = self._unknown_words(result["passage"], allowed)

        length_bad = (
            count < PASSAGE_ACCEPT_MIN or count > PASSAGE_ACCEPT_MAX
        )
        if missing or length_bad or len(unknown) > MAX_UNKNOWN_WORDS:
            print(
                f"  初稿 {count} 词，覆盖 {len(targets) - len(missing)}/"
                f"{len(targets)}，非目标超纲词 {len(unknown)} 个；"
                "正在让 AI 修正一次..."
            )
            repair = messages + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "Revise the article and return a complete new JSON "
                        "object with the same keys. "
                        f"It must be {PASSAGE_MIN_WORDS}-{PASSAGE_MAX_WORDS} "
                        f"words. It missed these target words: "
                        f"{', '.join(missing) or 'none'}. These non-target "
                        f"words are too difficult for this learner: "
                        f"{', '.join(unknown[:12]) or 'none'}. Replace them "
                        "with simple everyday words, use every target word "
                        "naturally, and keep the same topic. Return JSON only."
                    ),
                },
            ]
            try:
                content2 = self._call_deepseek(repair, key, model, base_url)
                result2 = self._normalize_result(
                    self._parse_json(content2), targets
                )
                _, missing2, count2 = self._score_result(result2, targets)
                unknown2 = self._unknown_words(result2["passage"], allowed)
                quality = (
                    len(targets) - len(missing),
                    -len(unknown),
                    -abs(count - PASSAGE_TARGET_WORDS),
                )
                quality2 = (
                    len(targets) - len(missing2),
                    -len(unknown2),
                    -abs(count2 - PASSAGE_TARGET_WORDS),
                )
                if quality2 > quality:
                    result, missing, count = result2, missing2, count2
                    unknown = unknown2
            except DailyPassageError as exc:
                print(f"  修正失败，保留初稿：{exc}")

        if missing:
            print(
                f"  {C_ERR}注意：仍有 {len(missing)} 个目标词未覆盖："
                f"{', '.join(missing)}{C_RESET}"
            )
        if len(unknown) > MAX_UNKNOWN_WORDS:
            print(
                f"  {C_ERR}注意：仍有 {len(unknown)} 个非目标超纲词："
                f"{', '.join(unknown[:12])}{C_RESET}"
            )
        return result, model

    # ------------------------------------------------------------ 生成与保存

    def _generate_and_display(self, targets, force=False):
        if not targets:
            print("\n学习列表为空，请先添加并学习单词。")
            return
        cached = self._current_cached()
        if not force and cached is not None:
            self._display(cached)
            return

        key = self._require_key()
        if not key:
            return

        print(f"\n正在匹配本地雅思真题素材（目标词 {len(targets)} 个）...")
        source = self.find_ielts_source(targets)
        if source:
            print(
                f"匹配到《{source['title']}》"
                f"（{source['word_count']} 词节选，覆盖 "
                f"{len(source['matched'])}/{len(targets)} 个目标词），"
                "将交给 AI 改编。"
            )
        else:
            print("没有足够的真题匹配，改为 AI 原创雅思风格短文。")

        model, _ = self._api_settings()
        print(f"正在调用 DeepSeek（{model}）生成约 300 词短文...")
        try:
            result, model = self._generate_with_ai(targets, source, key)
        except DailyPassageError as exc:
            print(f"\n生成失败：{exc}")
            return

        notes = {
            item["word"].lower(): result["word_sentences"].get(
                item["word"].lower(), ""
            )
            for item in targets
        }
        row = {
            "local_date": self._today(),
            "title": result["title"],
            "topic": result["topic"],
            "body": result["passage"],
            "translation": result["translation"],
            "source_type": "ielts" if source else "ai",
            "source_id": source["id"] if source else "",
            "source_title": source["title"] if source else "",
            "source_excerpt": source["text"] if source else "",
            "target_words": targets,
            "word_notes": notes,
            "model": model,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save(row)
        print(f"已保存今日短文（{_word_count(row['body'])} 词）")
        self._display(row)

    def _save(self, row):
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_passage (
                        local_date, title, topic, body, translation,
                        source_type, source_id, source_title, source_excerpt,
                        target_words, word_notes, model, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["local_date"],
                        row["title"],
                        row["topic"],
                        row["body"],
                        row["translation"],
                        row["source_type"],
                        row["source_id"],
                        row["source_title"],
                        row["source_excerpt"],
                        json.dumps(row["target_words"], ensure_ascii=False),
                        json.dumps(row["word_notes"], ensure_ascii=False),
                        row["model"],
                        row["created_at"],
                    ),
                )
        except sqlite3.Error as exc:
            print(f"\n保存短文失败：{exc}")

    # ------------------------------------------------------------ 显示

    def _row_value(self, row, key, default=""):
        try:
            value = row[key]
        except (IndexError, KeyError, TypeError):
            return default
        return default if value is None else value

    def _cached_targets(self, row):
        raw = self._row_value(row, "target_words", "[]")
        if isinstance(raw, list):
            data = raw
        else:
            try:
                data = json.loads(raw or "[]")
            except (ValueError, TypeError):
                return []
        if not isinstance(data, list):
            return []
        targets = []
        for item in data:
            if isinstance(item, dict) and item.get("word"):
                targets.append({
                    "word": str(item["word"]),
                    "meaning": str(item.get("meaning") or ""),
                    "tag": str(item.get("tag") or ""),
                    "voc_id": str(item.get("voc_id") or ""),
                })
        return targets

    def _cached_notes(self, row):
        raw = self._row_value(row, "word_notes", "{}")
        if isinstance(raw, dict):
            return raw
        try:
            data = json.loads(raw or "{}")
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _highlight(self, line, words):
        patterns = []
        for word in words:
            patterns.extend(pattern.pattern for pattern in _patterns(word))
        if not patterns:
            return line
        combined = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
        return combined.sub(
            lambda match: f"{C_HEAD}{match.group(0)}{C_RESET}", line
        )

    def _open_reader(self, title, body):
        try:
            from reading_viewer import ConsoleReader, Dictionary
        except Exception as exc:
            print(f"分栏阅读不可用，改为普通显示：{exc}")
            return False
        dictionary = None
        try:
            dictionary = Dictionary(self.vocab_db_path)
            ConsoleReader(title or "每日短文", body, dictionary).run()
        except (RuntimeError, sqlite3.Error) as exc:
            print(f"分栏阅读不可用，改为普通显示：{exc}")
            return False
        except Exception as exc:
            print(f"阅读器启动失败，改为普通显示：{exc}")
            return False
        finally:
            if dictionary is not None:
                dictionary.close()
        return True

    def _display(self, row, use_reader=True):
        body = self._row_value(row, "body")
        title = self._row_value(row, "title", "A Daily IELTS Passage")
        topic = self._row_value(row, "topic")
        translation = self._row_value(row, "translation")
        source_type = self._row_value(row, "source_type")
        source_title = self._row_value(row, "source_title")
        targets = self._cached_targets(row)
        notes = self._cached_notes(row)
        words = [item["word"] for item in targets]

        matched, missing = [], []
        for word in words:
            (matched if _word_in_text(word, body) else missing).append(word)
        allowed = self._allowed_vocabulary(words)
        unknown = self._unknown_words(body, allowed)

        print("\n" + "=" * 60)
        print(f"{C_TITLE}{title}{C_RESET}")
        print("=" * 60)
        source_label = "雅思真题改编" if source_type == "ielts" else "DeepSeek 原创"
        print(f"日期：{self._date_display()} | 来源：{source_label}")
        if source_title:
            print(f"真题素材：《{source_title}》")
        if topic:
            print(f"主题：{topic}")
        print(
            f"正文：{_word_count(body)} 词 | "
            f"目标词覆盖：{len(matched)}/{len(words)}"
        )
        if unknown:
            print(
                f"非目标超纲词：{len(unknown)} 个"
                f"（{', '.join(unknown[:8])}"
                f"{' ...' if len(unknown) > 8 else ''}）"
            )
        else:
            print("非目标超纲词：0 个")
        if missing:
            print(f"{C_ERR}未覆盖：{', '.join(missing)}{C_RESET}")
        print("-" * 60)

        words_set = set(words)
        width = _console_width()
        if use_reader and self._open_reader(title, body):
            print("已退出分栏阅读器。")
        else:
            for paragraph in str(body).split("\n"):
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                for line in _wrap_english(paragraph, width=width).split("\n"):
                    print(self._highlight(line, words_set))
                print()

        print("-" * 60)
        print("本次目标词")
        for item in targets:
            word = item["word"]
            sentence = notes.get(word.lower(), "")
            meaning = f"  {item['meaning']}" if item["meaning"] else ""
            print(f"  {C_OK}{word}{C_RESET}{meaning}")
            if sentence:
                print(_wrap_english(sentence, width=72, indent="    "))
        print("-" * 60)

        if translation:
            print("参考译文")
            print("-" * 60)
            print(_wrap_cjk(translation, max_cols=width))
            print("-" * 60)

        print("=" * 60)
        input("回车返回...")


def main():
    DailyPassage().run()


if __name__ == "__main__":
    main()
