# -*- coding: utf-8 -*-
import contextlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from daily_passage import (
    DailyPassage, DailyPassageError, _sanitize_api_key, _word_in_text,
)

NEW_WORDS = [
    "resource", "urgent", "exist", "evidence", "equal",
    "design", "calm", "regulate", "adapt", "external",
]
REVIEW_WORDS = ["technique", "beneficial", "crawl", "aware", "severe"]
TARGET_WORDS = NEW_WORDS + REVIEW_WORDS

MEANINGS = {
    "resource": "n. 财力；资源；谋略",
    "urgent": "adj. 紧急的，迫切的",
    "exist": "v. 存在；生存",
    "evidence": "n. 根据；证据；迹象",
    "equal": "adj. 相同的；平等的",
    "design": "n. 设计；构思 v. 设计",
    "calm": "adj. 镇静的 v. 使平静",
    "regulate": "v. 管理；调节",
    "adapt": "v. 调整，使适应",
    "external": "adj. 外部的，外来的",
    "technique": "n. 工艺；技术，技能",
    "beneficial": "adj. 有帮助的，有利的",
    "crawl": "v. 爬行；缓慢地行进",
    "aware": "adj. 知道的；察觉的",
    "severe": "adj. 严重的；严厉的",
}

READING_BODY = (
    "READING PASSAGE 1 You should spend about 20 minutes on Questions 1-13 "
    "below. Design is often treated as a matter of taste, but the evidence "
    "suggests otherwise. An external observer may notice only the surface of "
    "a building, yet the choices behind it affect how people move, talk and "
    "rest. Researchers examined several offices and found that a calm layout "
    "was beneficial for concentration. Technique mattered less than the basic "
    "arrangement of space. Designers who adapt a room to its users often "
    "produce better results than those who follow a fixed rule. Resources are "
    "limited, so every decision involves a trade-off. A severe lack of light, "
    "for example, can make a workplace feel uncomfortable. Staff soon become "
    "aware of small changes, and they regulate their behaviour accordingly. "
    "Good design is therefore a practical resource rather than a luxury, and "
    "the evidence for this view is now difficult to ignore."
)

FAKE_PASSAGE = (
    "Modern offices face an urgent question. How should a room be designed "
    "when external pressure never stops? A useful answer starts with a simple "
    "idea. Every worker is equal in the need for a calm place to think, yet "
    "each person adapts to noise in a different way. This is not only about "
    "comfort. Evidence shows that constant noise has a severe effect on focus, "
    "and the technique of deep work cannot exist without quiet time.\n\n"
    "Designers in a small town started from this rule. They wanted a space "
    "that would regulate sound without costly technology. The result is "
    "beneficial in several ways. Quiet rooms give people a place to work when "
    "a task is urgent, while open tables help a team share resources. Staff "
    "soon became aware of how their habits affected others. Change was slow "
    "at first; progress seemed to crawl. In time, the company reported better "
    "focus."
)

FAKE_RESULT = {
    "title": "Designing a Calm Workplace",
    "topic": "workplace design / attention",
    "passage": FAKE_PASSAGE,
    "translation": "现代公司面临一个紧迫的问题：当外部压力从不停止时，办公室应该如何设计？"
                   "一个有用的答案始于一个简单的想法。每个员工都同样需要一个安静思考的地方，"
                   "但每个人适应噪音的方式不同。",
    "word_sentences": {
        "urgent": "Modern offices face an urgent question.",
        "calm": "Every worker is equal in the need for a calm place to think, "
                "yet each person adapts to noise in a different way.",
        "crawl": "Change was slow at first; progress seemed to crawl.",
    },
}


class DailyPassageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.study_db = os.path.join(self.tmp.name, "study.db")
        self.vocab_db = os.path.join(self.tmp.name, "word_learn.db")
        self.config = os.path.join(self.tmp.name, "user_config.json")
        self._build_study_db()
        self._build_vocab_db()
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump({"deepseek_api_key": "sk-test-secret-123456"}, handle)

        env = {
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": "",
            "DEEPSEEK_MODEL": "",
        }
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        self.daily = DailyPassage(self.study_db, self.vocab_db, self.config)
        self.addCleanup(self.daily.close)

    def _build_study_db(self):
        conn = sqlite3.connect(self.study_db)
        conn.executescript(
            """
            CREATE TABLE study_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voc_id TEXT UNIQUE,
                vocabulary TEXT,
                add_date TEXT,
                first_study_date TEXT,
                last_review_at INTEGER,
                lapses INTEGER,
                state INTEGER,
                stability REAL
            );
            CREATE TABLE review_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                voc_id TEXT,
                rating INTEGER,
                reviewed_at INTEGER,
                local_date TEXT
            );
            CREATE TABLE reading_passage (
                id TEXT PRIMARY KEY,
                title TEXT,
                category TEXT,
                frequency TEXT,
                body_text TEXT
            );
            """
        )
        today = self.daily_today()
        for index, word in enumerate(NEW_WORDS, 1):
            conn.execute(
                "INSERT INTO study_list (voc_id, vocabulary, add_date, "
                "first_study_date, last_review_at, lapses, state, stability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"new-{index}", word, today, today, 0, 0, 2, 5.0),
            )
        for index, word in enumerate(REVIEW_WORDS, 1):
            conn.execute(
                "INSERT INTO study_list (voc_id, vocabulary, add_date, "
                "first_study_date, last_review_at, lapses, state, stability) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"old-{index}", word, "20260101", "20260101",
                 1790000000 + index, index, 2, 2.0),
            )
            conn.execute(
                "INSERT INTO review_log (voc_id, rating, reviewed_at, local_date) "
                "VALUES (?, ?, ?, ?)",
                (f"old-{index}", index, 1790000000 + index, today),
            )
        conn.execute(
            "INSERT INTO reading_passage (id, title, category, frequency, body_text) "
            "VALUES (?, ?, ?, ?, ?)",
            ("p1-test-01", "The Design of Everyday Spaces", "P1", "high",
             READING_BODY),
        )
        conn.commit()
        conn.close()

    def _build_vocab_db(self):
        conn = sqlite3.connect(self.vocab_db)
        conn.execute(
            "CREATE TABLE VOC_TB (vc_id TEXT PRIMARY KEY, "
            "vc_vocabulary TEXT, vc_interpretation TEXT)"
        )
        for index, word in enumerate(NEW_WORDS, 1):
            conn.execute(
                "INSERT INTO VOC_TB VALUES (?, ?, ?)",
                (f"new-{index}", word, MEANINGS[word]),
            )
        for index, word in enumerate(REVIEW_WORDS, 1):
            conn.execute(
                "INSERT INTO VOC_TB VALUES (?, ?, ?)",
                (f"old-{index}", word, MEANINGS[word]),
            )
        conn.execute(
            'CREATE TABLE BK_TB (id TEXT PRIMARY KEY, name TEXT, "order" REAL)'
        )
        conn.execute(
            'CREATE TABLE BK_VOC_TB (book_id TEXT, voc_id TEXT, "order" INTEGER)'
        )
        conn.execute(
            "INSERT INTO BK_TB VALUES ('coca-test', 'COCA 20000高频词', 1)"
        )
        for index, word in enumerate(("the", "office", "calm"), 1):
            conn.execute(
                "INSERT INTO VOC_TB VALUES (?, ?, ?)",
                (f"basic-{index}", word, word),
            )
            conn.execute(
                "INSERT INTO BK_VOC_TB VALUES ('coca-test', ?, ?)",
                (f"basic-{index}", index),
            )
        conn.commit()
        conn.close()

    @staticmethod
    def daily_today():
        from datetime import datetime
        return datetime.now().strftime("%Y%m%d")

    # ---------------------------------------------------------------- 选词

    def test_select_target_words_prioritizes_new_then_reviewed(self):
        targets = self.daily.select_target_words(15)
        self.assertEqual(len(targets), 15)
        self.assertEqual([item["tag"] for item in targets[:10]], ["新学"] * 10)
        self.assertEqual([item["tag"] for item in targets[10:]], ["今日复习"] * 5)
        self.assertEqual({item["word"] for item in targets}, set(TARGET_WORDS))
        self.assertTrue(all(item["meaning"] for item in targets))

    def test_target_count_is_clamped(self):
        self.daily._save_config({"daily_passage_word_count": 99})
        self.assertEqual(self.daily._target_count(), 30)
        self.daily._save_config({"daily_passage_word_count": 1})
        self.assertEqual(self.daily._target_count(), 8)

    # ---------------------------------------------------------------- 词汇限制

    def test_learned_words_newest_first(self):
        words = self.daily._learned_words()
        self.assertEqual(words[:10], list(reversed(NEW_WORDS)))
        self.assertEqual(words[10:], list(reversed(REVIEW_WORDS)))

    def test_basic_words_reads_coca_book(self):
        basic = self.daily._basic_words()
        self.assertTrue({"the", "office", "calm"} <= basic)
        self.assertIn("an", basic)

    def test_allowed_vocabulary_includes_targets_learned_and_basic(self):
        allowed = self.daily._allowed_vocabulary(["design"])
        for word in ("design", "resource", "technique", "the", "office"):
            self.assertIn(word, allowed)

    def test_unknown_words_flags_only_out_of_list_terms(self):
        allowed = {
            "the", "office", "is", "calm", "and", "workers", "use",
            "simple", "words", "but",
        }
        unknown = self.daily._unknown_words(
            "The office is calm and workers use simple words, but mitigation.",
            allowed,
        )
        self.assertEqual(unknown, ["mitigation"])

    def test_unknown_words_allows_hyphenated_common_words(self):
        self.assertEqual(
            self.daily._unknown_words("long-term plan", {"long", "term", "plan"}),
            [],
        )

    def test_prompt_uses_short_length_and_known_words(self):
        targets = self.daily.select_target_words(15)
        prompt = self.daily._build_user_prompt(targets, None)
        self.assertIn("120-150", prompt)
        self.assertIn("LEARNER'S KNOWN WORDS", prompt)
        self.assertIn("newest to oldest", prompt)
        self.assertIn(NEW_WORDS[-1], prompt)

    # ---------------------------------------------------------------- 词形

    def test_inflections_are_recognized(self):
        self.assertTrue(_word_in_text("design", "designed"))
        self.assertTrue(_word_in_text("regulate", "regulating"))
        self.assertTrue(_word_in_text("crawl", "crawled"))
        self.assertTrue(_word_in_text("resource", "resources"))
        self.assertTrue(_word_in_text("severe", "severely"))

    def test_british_and_american_spellings(self):
        self.assertTrue(_word_in_text("fulfill", "fulfilled"))
        self.assertTrue(_word_in_text("fulfill", "fulfil"))
        self.assertTrue(_word_in_text("judgment", "judgement"))
        self.assertTrue(_word_in_text("favour", "favor"))

    # ---------------------------------------------------------------- 真题

    def test_reading_passage_intro_is_stripped(self):
        cleaned = self.daily._clean_passage_body(READING_BODY)
        self.assertTrue(cleaned.startswith("Design is often treated"))
        self.assertNotIn("READING PASSAGE", cleaned)

    def test_find_ielts_source_matches_target_words(self):
        targets = self.daily.select_target_words(15)
        source = self.daily.find_ielts_source(targets)
        self.assertIsNotNone(source)
        self.assertGreaterEqual(len(source["matched"]), 4)
        self.assertIn("design", source["matched"])

    # ---------------------------------------------------------------- 解析

    def test_parse_json_accepts_code_fence(self):
        data = self.daily._parse_json('```json\n{"passage": "hello"}\n```')
        self.assertEqual(data["passage"], "hello")

    def test_parse_json_rejects_non_json(self):
        with self.assertRaises(DailyPassageError):
            self.daily._parse_json("not json at all")

    def test_normalize_fills_missing_word_sentences(self):
        targets = self.daily.select_target_words(15)
        result = self.daily._normalize_result(FAKE_RESULT, targets)
        self.assertIn("design", result["word_sentences"])
        self.assertTrue(result["word_sentences"]["design"])
        self.assertIn("design", result["word_sentences"]["design"].lower())

    # ---------------------------------------------------------------- 生成

    def test_mock_generation_covers_all_targets(self):
        targets = self.daily.select_target_words(15)
        passage_words = set(re.findall(r"[A-Za-z]+", FAKE_PASSAGE.lower()))
        with patch.object(self.daily, "_basic_words", return_value=passage_words), \
                patch.object(
                    self.daily, "_call_deepseek",
                    return_value=json.dumps(FAKE_RESULT, ensure_ascii=False),
                ):
            result, model = self.daily._generate_with_ai(
                targets, source=None, key="sk-test"
            )
        _, missing, count = self.daily._score_result(result, targets)
        self.assertEqual(missing, [])
        self.assertGreaterEqual(count, 120)
        self.assertLessEqual(count, 150)
        self.assertEqual(model, "deepseek-flash")

    def test_cache_display_and_key_is_not_persisted(self):
        targets = self.daily.select_target_words(15)
        passage_words = set(re.findall(r"[A-Za-z]+", FAKE_PASSAGE.lower()))
        with patch.object(self.daily, "_basic_words", return_value=passage_words), \
                patch.object(
                    self.daily, "_call_deepseek",
                    return_value=json.dumps(FAKE_RESULT, ensure_ascii=False),
                ):
            result, model = self.daily._generate_with_ai(
                targets, source=None, key="sk-test"
            )
        notes = {
            item["word"].lower(): result["word_sentences"].get(
                item["word"].lower(), ""
            )
            for item in targets
        }
        self.daily._save({
            "local_date": self.daily_today(),
            "title": result["title"],
            "topic": result["topic"],
            "body": result["passage"],
            "translation": result["translation"],
            "source_type": "ai",
            "source_id": "",
            "source_title": "",
            "source_excerpt": "",
            "target_words": targets,
            "word_notes": notes,
            "model": model,
            "created_at": "test",
        })

        cached = self.daily._get_cached(self.daily_today())
        self.assertIsNotNone(cached)
        self.assertEqual(len(self.daily._cached_targets(cached)), 15)
        self.assertEqual(len(self.daily._cached_notes(cached)), 15)

        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("builtins.input", return_value=""):
            self.daily._display(cached, use_reader=False)
        text = output.getvalue()
        self.assertIn("Designing a Calm Workplace", text)
        self.assertIn("目标词覆盖：15/15", text)
        self.assertIn("参考译文", text)

        dump = "\n".join(
            str(value)
            for row in self.daily.conn.execute("SELECT * FROM daily_passage")
            for value in row
        )
        self.assertNotIn("sk-test-secret", dump)

    def test_key_can_be_cleared(self):
        self.daily._save_config({"deepseek_api_key": ""})
        key, _ = self.daily._resolve_api_key()
        self.assertEqual(key, "")

    # ---------------------------------------------------------------- Key 清洗

    def test_sanitize_api_key_removes_terminal_artifacts(self):
        clean = "sk-" + "a" * 32
        raw = "Bearer `" + clean + "`\x1b[0m"
        self.assertEqual(_sanitize_api_key(raw), clean)
        self.assertEqual(_sanitize_api_key(clean + "\x1b\x1b"), clean)
        self.assertEqual(_sanitize_api_key("  " + clean + "  "), clean)

    def test_resolve_sanitizes_stored_key(self):
        clean = "sk-" + "b" * 32
        self.daily._save_config({"deepseek_api_key": "`" + clean + "`\x1b[0m"})
        key, source = self.daily._resolve_api_key()
        self.assertEqual(key, clean)
        self.assertEqual(source, "本地 user_config.json")

    def test_configure_key_rejects_malformed_paste(self):
        with patch(
            "daily_passage.getpass.getpass",
            return_value="`not-a-key`\x1b[0m",
        ), patch.object(self.daily, "_save_config") as save:
            with contextlib.redirect_stdout(io.StringIO()):
                self.daily._configure_key()
        save.assert_not_called()

    def test_configure_key_does_not_save_key_rejected_by_api(self):
        with patch(
            "daily_passage.getpass.getpass",
            return_value="sk-" + "c" * 32,
        ), patch.object(
            self.daily, "_verify_key", return_value=(False, "Key 无效")
        ), patch.object(self.daily, "_save_config") as save:
            with contextlib.redirect_stdout(io.StringIO()):
                self.daily._configure_key()
        save.assert_not_called()

    def test_bad_request_message_mentions_key_copy(self):
        exc = urllib.error.HTTPError(
            "https://api.deepseek.com",
            400,
            "Bad Request",
            {},
            io.BytesIO(b"Bad Request"),
        )
        message = self.daily._http_error_message(exc)
        exc.close()
        self.assertIn("API Key", message)
        self.assertIn("反引号", message)

    # ---------------------------------------------------------------- 分栏阅读

    def test_display_opens_reader_by_default(self):
        row = {
            "body": FAKE_PASSAGE,
            "title": "Designing a Calm Workplace",
            "target_words": [],
            "word_notes": {},
            "translation": "",
        }
        with patch.object(
            self.daily, "_open_reader", return_value=True
        ) as reader, patch("builtins.input", return_value=""), \
                contextlib.redirect_stdout(io.StringIO()):
            self.daily._display(row)
        reader.assert_called_once()

    def test_open_reader_uses_vocab_db(self):
        with patch("reading_viewer.ConsoleReader") as reader_cls, patch(
            "reading_viewer.Dictionary"
        ) as dict_cls:
            ok = self.daily._open_reader("Test title", "Some body")
        self.assertTrue(ok)
        args = reader_cls.call_args.args
        self.assertEqual(args[0], "Test title")
        self.assertEqual(args[1], "Some body")
        dict_cls.assert_called_once_with(self.vocab_db)
        reader_cls.return_value.run.assert_called_once()
        dict_cls.return_value.close.assert_called_once()

    def test_open_reader_falls_back_without_console(self):
        with patch("reading_viewer.ConsoleReader") as reader_cls, patch(
            "reading_viewer.Dictionary"
        ) as dict_cls:
            reader_cls.return_value.run.side_effect = RuntimeError("no console")
            with contextlib.redirect_stdout(io.StringIO()):
                ok = self.daily._open_reader("Test title", "Some body")
        self.assertFalse(ok)
        dict_cls.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
