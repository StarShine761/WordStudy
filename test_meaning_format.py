# -*- coding: utf-8 -*-
import unittest

from meaning_format import format_meaning, format_option, meaning_lines


class MeaningLineTests(unittest.TestCase):
    def test_splits_concatenated_parts_of_speech(self):
        text = ("adj.昏暗的，朦胧的；晦涩的vt.使…模糊不清n.某种模糊的东西")
        self.assertEqual(
            meaning_lines(text),
            ["adj. 昏暗的，朦胧的；晦涩的", "vt. 使…模糊不清", "n. 某种模糊的东西"],
        )

    def test_keeps_summary_and_existing_lines(self):
        text = "那\npron. 那个，那些\nadj. 那个的，那些的"
        self.assertEqual(
            meaning_lines(text),
            ["那", "pron. 那个，那些", "adj. 那个的，那些的"],
        )

    def test_does_not_split_nested_synonym_notes(self):
        text = "n. 商品，货物；日用品【同义】goods n. 货物"
        self.assertEqual(
            meaning_lines(text),
            ["n. 商品，货物；日用品【同义】goods n. 货物"],
        )

    def test_does_not_split_pos_inside_parentheses(self):
        text = "n. 生产力；生产率【记忆】来自product（n. 产品）"
        self.assertEqual(
            meaning_lines(text),
            ["n. 生产力；生产率【记忆】来自product（n. 产品）"],
        )

    def test_normalizes_short_pos_labels(self):
        self.assertEqual(meaning_lines("a.英国的"), ["adj. 英国的"])
        self.assertEqual(meaning_lines("ad. 非常"), ["adv. 非常"])

    def test_merges_repeated_parts_of_speech(self):
        self.assertEqual(
            meaning_lines("n. 苹果\nn. 苹果树"),
            ["n. 苹果；苹果树"],
        )

    def test_empty_text(self):
        self.assertEqual(meaning_lines(""), [])

    def test_format_meaning_indents_following_lines(self):
        text = "深刻的\nadj. 深切的\nv. 使深刻"
        self.assertEqual(
            format_meaning(text, first_prefix="   释义: ", cont_prefix="         "),
            "   释义: 深刻的\n         adj. 深切的\n         v. 使深刻",
        )

    def test_format_option_aligns_under_option_text(self):
        text = "adj. 昏暗的\nvt. 使模糊"
        self.assertEqual(
            format_option("1", text, indent="  "),
            "  1. adj. 昏暗的\n     vt. 使模糊",
        )


if __name__ == "__main__":
    unittest.main()
