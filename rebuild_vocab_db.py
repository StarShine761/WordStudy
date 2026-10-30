#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the vocabulary database (word_learn.db) from current open word lists.

Sources:
  - exam-data/NETEMVocabulary   (考研大纲 5530, 2026-10 update)
  - exam-data/CETVocabulary     (四六级大纲 5278, 2026-10 update)
  - RealKai42/qwerty-learner    (2026红宝书 / 2025考研 / 雅思 / 牛津 / COCA ...)
  - skywind3000/ECDICT          (phonetic + definition fallback)

The old database is moved to data/backup/ instead of being deleted, so the
rebuild can be rolled back. word_study.db (study records + reading bank) is
reset as requested.
"""

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
DATA = BASE / "data" / "wordlists"
BACKUP = BASE / "data" / "backup"
OLD_DB = BASE / "word_learn.db"
STUDY_DB = BASE / "word_study.db"
TMP_DB = BASE / "word_learn.new.db"

EXAM_RAW = "https://raw.githubusercontent.com/exam-data/{repo}/master/{file}"
QWERTY_RAW = "https://raw.githubusercontent.com/RealKai42/qwerty-learner/master/public/dicts/{file}"
ECDICT_RAW = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"

EXAM_FILES = {
    "netem_reranked.json": "NETEMVocabulary",
    "netem_beyond_ks3000.json": "NETEMVocabulary",
    "cet_reranked.json": "CETVocabulary",
    "cet_beyond_ks3000.json": "CETVocabulary",
}

QWERTY_BOOKS = [
    ("hongbaoshu-2026.json", "hongbaoshu_2026", "2026红宝书考研词汇",
     "2026 版考研英语红宝书（必考词+基础词+超纲词）", 6),
    ("2025KaoYanHongBaoShu.json", "hongbaoshu_2025", "2025考研红宝书",
     "2025 版考研英语红宝书", 6),
    ("kaoyanshanguo2025.json", "kaoyan_shanguo_2025", "2025考研词汇闪过",
     "2025 版考研词汇闪过", 6),
    ("KaoYan_2024.json", "kaoyan_2024", "2024考研词汇",
     "2024 版考研英语词汇", 6),
    ("IELTS_XDF_7000.json", "ielts_xdf_7000", "雅思词汇7000（新东方）",
     "新东方雅思 7000 词汇", 8),
    ("GaoKao_3500.json", "gaokao_3500", "高考英语3500词",
     "高考英语大纲 3500 词", 3),
    ("ZhongKaoHeXin.json", "zhongkao_core", "中考核心词汇",
     "中考英语核心词汇", 2),
    ("Oxford3000.json", "oxford_3000", "牛津3000核心词",
     "Oxford 3000 核心词汇", 9),
    ("Oxford5000.json", "oxford_5000", "牛津5000核心词",
     "Oxford 5000 核心词汇", 9),
    ("coca20000.json", "coca_20000", "COCA 20000高频词",
     "美国当代英语语料库（COCA）词频前 20000", 9),
]

EXAM_BOOK_KEYS = {
    "netem_reranked.json": "netem_5530",
    "netem_beyond_ks3000.json": "netem_beyond_3000",
    "cet_reranked.json": "cet_5278",
    "cet_beyond_ks3000.json": "cet_beyond_3000",
}

CEFR_SCORE = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}


def _file_valid(path: Path, kind: str) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if kind == "json":
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
            return True
        except Exception:
            return False
    if kind == "ecdict":
        if path.stat().st_size < 10_000_000:
            return False
        try:
            with open(path, encoding="utf-8", errors="strict") as f:
                return f.readline().startswith("word,phonetic")
        except Exception:
            return False
    return True


def download(url: str, dest: Path, kind: str = "json") -> None:
    if _file_valid(dest, kind):
        return
    req = urllib.request.Request(url, headers={"User-Agent": "word-study-rebuild/1.0"})
    last_error = None
    for attempt in range(3):
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                expected = resp.headers.get("Content-Length")
                with open(tmp, "wb") as f:
                    shutil.copyfileobj(resp, f, length=1024 * 256)
            got = tmp.stat().st_size
            if expected and got != int(expected):
                raise IOError(f"incomplete download {got}/{expected} bytes")
            if not _file_valid(tmp, kind):
                raise IOError("downloaded file failed validation")
            tmp.replace(dest)
            print(f"  downloaded {dest.name} ({got / 1024 / 1024:.1f} MB)")
            return
        except Exception as e:
            last_error = e
            if tmp.exists():
                tmp.unlink()
            time.sleep(1 + attempt)
    raise RuntimeError(f"failed to download {url}: {last_error}")


def prepare_sources() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    print("Checking word list sources ...")
    for name, repo in EXAM_FILES.items():
        download(EXAM_RAW.format(repo=repo, file=name), DATA / name)
    for file_name, *_ in QWERTY_BOOKS:
        download(QWERTY_RAW.format(file=file_name), DATA / file_name)
    download(ECDICT_RAW, DATA / "ecdict.csv", kind="ecdict")


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def exam_entries(path: Path) -> list:
    data = load_json(path)
    if isinstance(data.get("词汇表"), list):
        return data["词汇表"]
    for value in data.values():
        if isinstance(value, list):
            return value
    return []


class WordBag:
    def __init__(self):
        self.words = {}

    def add(self, word, meaning="", us="", uk="", cefr="", collins=0,
            example="", example_cn="", origin="", priority=50):
        if not word:
            return
        word = str(word).strip()
        if not word:
            return
        key = word.lower()
        w = self.words.get(key)
        if w is None:
            w = {
                "word": word,
                "meanings": [],
                "best_priority": -1,
                "us": "",
                "uk": "",
                "cefr": "",
                "collins": 0,
                "examples": [],
            }
            self.words[key] = w

        meaning = (meaning or "").strip()
        if meaning:
            w["meanings"].append((priority, meaning))
            if priority > w["best_priority"]:
                w["best_priority"] = priority

        if us and not w["us"]:
            w["us"] = us.strip()
        if uk and not w["uk"]:
            w["uk"] = uk.strip()
        if cefr and not w["cefr"]:
            w["cefr"] = cefr.strip().upper()
        if collins and not w["collins"]:
            try:
                w["collins"] = int(collins)
            except (TypeError, ValueError):
                pass

        example = (example or "").strip()
        if example and len(w["examples"]) < 3:
            item = (example, (example_cn or "").strip(), (origin or "").strip())
            if item[0] not in [e[0] for e in w["examples"]]:
                w["examples"].append(item)

    def interpretation(self, key: str) -> str:
        w = self.words[key]
        # Prefer the highest-priority source; if it is very terse, append the
        # richest lower-priority meaning so the card is still informative.
        ranked = sorted(w["meanings"], key=lambda x: (-x[0], -len(x[1])))
        if not ranked:
            return ""
        parts = [ranked[0][1]]
        if len(ranked[0][1]) < 10:
            for _, m in ranked[1:]:
                if m and m not in parts:
                    parts.append(m)
                    break
        return "\n".join(parts)[:600]


def normalize_phone(value: str) -> str:
    if not value:
        return ""
    value = value.strip().strip("/").strip()
    return f"/{value}/" if value else ""


def clean_ecdict_translation(value: str) -> str:
    if not value:
        return ""
    lines = []
    for line in value.splitlines():
        line = line.strip()
        if not line or line.startswith("[网络]") or line.startswith("网络"):
            continue
        lines.append(line)
        if len(lines) >= 2:
            break
    return "\n".join(lines)[:400]


def build_words() -> tuple[WordBag, list]:
    bag = WordBag()
    books = []

    def add_exam_book(file_name, book_key, name, full_name, level, priority):
        entries = exam_entries(DATA / file_name)
        seen = set()
        for item in entries:
            word = item.get("单词")
            if not word:
                continue
            bag.add(
                word,
                meaning=item.get("释义") or "",
                cefr=item.get("CEFR等级") or "",
                example=item.get("例句") or "",
                example_cn=item.get("例句翻译") or "",
                origin=item.get("例句来源") or "",
                priority=priority,
            )
            seen.add(str(word).strip().lower())
        books.append((book_key, name, full_name, level, len(seen), "exam-data"))

    add_exam_book("netem_reranked.json", "netem_5530",
                  "考研英语大纲词汇5500（词频重排）",
                  "2024 考研英语（一）大纲 5530 词 · 按真题词频智能重排",
                  6, 100)
    add_exam_book("netem_beyond_ks3000.json", "netem_beyond_3000",
                  "考研大纲·超课标词2700",
                  "考研大纲 5530 词剔除高中课标 3000 词后的 2697 词",
                  6, 95)
    add_exam_book("cet_reranked.json", "cet_5278",
                  "大学英语四六级大纲词汇（词频重排）",
                  "2016 修订版四六级大纲 5278 词 · 按真题词频智能重排",
                  4, 100)
    add_exam_book("cet_beyond_ks3000.json", "cet_beyond_3000",
                  "四六级大纲·超课标词2700",
                  "四六级大纲 5278 词剔除高中课标 3000 词后的 2734 词",
                  4, 95)

    for file_name, key, name, full_name, level in QWERTY_BOOKS:
        data = load_json(DATA / file_name)
        seen = set()
        for item in data:
            word = item.get("name")
            if not word:
                continue
            trans = item.get("trans") or ""
            if isinstance(trans, list):
                trans = "\n".join(str(t) for t in trans if t)
            bag.add(
                word,
                meaning=trans,
                us=normalize_phone(item.get("usphone") or ""),
                uk=normalize_phone(item.get("ukphone") or ""),
                priority=90 if level in (6, 8) else 70,
            )
            seen.add(str(word).strip().lower())
        books.append((key, name, full_name, level, len(seen), "qwerty-learner"))

    # ECDICT only enriches words already present; it never adds new entries.
    print("Merging ECDICT phonetics / definitions ...")
    with open(DATA / "ecdict.csv", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("word") or "").strip().lower()
            w = bag.words.get(key)
            if not w:
                continue
            if not w["us"]:
                phone = normalize_phone(row.get("phonetic") or "")
                w["us"] = phone
            if not w["uk"]:
                phone = normalize_phone(row.get("phonetic") or "")
                if phone:
                    w["uk"] = phone
            if not w["meanings"]:
                meaning = clean_ecdict_translation(row.get("translation") or "")
                if meaning:
                    w["meanings"].append((10, meaning))
                    w["best_priority"] = 10
            if not w["collins"]:
                try:
                    w["collins"] = int(row.get("collins") or 0)
                except ValueError:
                    pass

    return bag, books


def make_id(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:24]


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE BK_TB (
            id TEXT PRIMARY KEY,
            original_id TEXT,
            catalog_id TEXT,
            level INTEGER,
            "order" REAL,
            name TEXT,
            full_name TEXT,
            voc_count INTEGER,
            author TEXT,
            comment TEXT,
            isbn TEXT,
            organization TEXT,
            publisher TEXT,
            version TEXT,
            flag TEXT,
            urls TEXT,
            status TEXT
        );
        CREATE TABLE VOC_TB (
            vc_id TEXT PRIMARY KEY,
            original_id TEXT,
            vc_vocabulary TEXT NOT NULL,
            vc_initial TEXT,
            vc_phonetic_uk TEXT,
            vc_phonetic_us TEXT,
            vc_interpretation TEXT,
            vc_pronunciation TEXT,
            vc_frequency INTEGER DEFAULT 0,
            vc_difficulty REAL DEFAULT 0,
            vc_phrase TEXT,
            vc_updated_time TEXT,
            vc_study_user_count INTEGER DEFAULT 0,
            vc_acknowledge_rate REAL DEFAULT 0
        );
        CREATE TABLE BK_VOC_TB (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id TEXT,
            voc_id TEXT,
            chapter_id TEXT,
            flag TEXT,
            "order" INTEGER
        );
        CREATE TABLE PR_TB (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voc_id TEXT,
            phrase TEXT,
            interpretation TEXT,
            origin TEXT,
            highlight TEXT,
            creator TEXT,
            owner TEXT,
            like_user_count INTEGER DEFAULT 0,
            like INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()


def write_db(bag: WordBag, books: list) -> None:
    if TMP_DB.exists():
        TMP_DB.unlink()
    conn = sqlite3.connect(TMP_DB)
    create_schema(conn)
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    catalog = "study-wordlists"

    vid = {}
    word_rows = []
    pr_rows = []
    for key, w in bag.words.items():
        wid = make_id(key)
        vid[key] = wid
        word_rows.append((
            wid,
            None,
            w["word"][:100],
            (w["word"][0].upper() if w["word"] else ""),
            w["uk"],
            w["us"],
            bag.interpretation(key),
            "",
            0,
            CEFR_SCORE.get(w["cefr"], w["collins"] or 0),
            w["examples"][0][0][:500] if w["examples"] else "",
            now,
            0,
            0.0,
        ))
        for phrase, cn, origin in w["examples"]:
            pr_rows.append((wid, phrase[:800], cn[:800], origin[:100]))

    cur.executemany(
        """INSERT INTO VOC_TB
           (vc_id, original_id, vc_vocabulary, vc_initial, vc_phonetic_uk,
            vc_phonetic_us, vc_interpretation, vc_pronunciation, vc_frequency,
            vc_difficulty, vc_phrase, vc_updated_time, vc_study_user_count,
            vc_acknowledge_rate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        word_rows,
    )
    cur.executemany(
        """INSERT INTO PR_TB (voc_id, phrase, interpretation, origin)
           VALUES (?,?,?,?)""",
        pr_rows,
    )

    book_rows = []
    bkvoc_rows = []
    for idx, (key, name, full_name, level, count, source) in enumerate(books, start=1):
        bid = make_id(f"book:{key}")
        book_rows.append((
            bid, None, catalog, level, float(idx), name, full_name,
            count, source, f"来源: {source}", None, None, None,
            datetime.now().strftime("%Y-%m"), None, None, None,
        ))
    cur.executemany(
        """INSERT INTO BK_TB
           (id, original_id, catalog_id, level, "order", name, full_name,
            voc_count, author, comment, isbn, organization, publisher,
            version, flag, urls, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        book_rows,
    )

    book_ids = {key: make_id(f"book:{key}") for key, *_ in books}
    book_orders = {key: 0 for key, *_ in books}

    def link_book(book_key, word):
        key = str(word).strip().lower()
        wid = vid.get(key)
        if wid:
            book_orders[book_key] = book_orders.get(book_key, 0) + 1
            bkvoc_rows.append((book_ids[book_key], wid, None, None, book_orders[book_key]))

    for name in EXAM_FILES:
        for item in exam_entries(DATA / name):
            link_book(EXAM_BOOK_KEYS[name], item.get("单词") or "")
    for file_name, key, *_ in QWERTY_BOOKS:
        for item in load_json(DATA / file_name):
            link_book(key, item.get("name") or "")

    cur.executemany(
        'INSERT INTO BK_VOC_TB (book_id, voc_id, chapter_id, flag, "order") VALUES (?,?,?,?,?)',
        bkvoc_rows,
    )
    cur.executescript(
        """
        CREATE INDEX idx_voc_word ON VOC_TB(vc_vocabulary COLLATE NOCASE);
        CREATE INDEX idx_pr_voc ON PR_TB(voc_id);
        CREATE INDEX idx_bkvoc_book ON BK_VOC_TB(book_id);
        CREATE INDEX idx_bkvoc_voc ON BK_VOC_TB(voc_id);
        """
    )
    conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM VOC_TB").fetchone()[0]
    phr = cur.execute("SELECT COUNT(*) FROM PR_TB").fetchone()[0]
    conn.close()
    print(f"Built {TMP_DB.name}: {total} unique words, {phr} example sentences, {len(books)} books")


def backup_and_swap() -> None:
    BACKUP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if OLD_DB.exists():
        dest = BACKUP / f"word_learn.db.bak-{stamp}"
        shutil.move(str(OLD_DB), str(dest))
        print(f"Old vocabulary db moved to {dest.relative_to(BASE)}")
    TMP_DB.replace(OLD_DB)

    if STUDY_DB.exists():
        dest = BACKUP / f"word_study.db.bak-{stamp}"
        shutil.move(str(STUDY_DB), str(dest))
        print(f"Old study db moved to {dest.relative_to(BASE)}")

    # Create the empty study tables the app expects (schema v2 / FSRS).
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from srs.schema import ensure_schema

    ensure_schema(STUDY_DB)
    conn = sqlite3.connect(STUDY_DB)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS phrases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            english TEXT,
            chinese TEXT,
            add_date TEXT
        );
        CREATE TABLE IF NOT EXISTS wrong_words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voc_id TEXT,
            vocabulary TEXT,
            interpretation TEXT,
            phonetic_us TEXT,
            add_date TEXT,
            wrong_count INTEGER DEFAULT 1,
            last_wrong_date TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    # Clear stale book selection while keeping user preferences.
    cfg_path = BASE / "user_config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg.pop("last_book_id", None)
        cfg.pop("last_book_name", None)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    t0 = time.time()
    prepare_sources()
    bag, books = build_words()
    if len(bag.words) < 1000:
        raise SystemExit("Not enough words parsed, aborting")
    write_db(bag, books)
    backup_and_swap()
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
