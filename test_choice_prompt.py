import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault('edge_tts', types.SimpleNamespace())

from word_study import WordStudyApp, WRONG_ICON


def _has_icon(text):
    """是否含 emoji / 符号类图标（与项目图标清理时使用的范围一致）"""
    return any(0x1F000 <= ord(ch) <= 0x1FAFF
               or 0x2600 <= ord(ch) <= 0x27BF
               or 0x2B00 <= ord(ch) <= 0x2BFF
               for ch in text)


class ChoicePromptTests(unittest.TestCase):
    def test_prompt_retries_until_valid_choice(self):
        app = WordStudyApp.__new__(WordStudyApp)

        with patch('builtins.input', side_effect=['invalid', '4']):
            choice = app._prompt_choice()

        self.assertEqual(choice, '4')

    def test_prompt_rejects_five_and_six_and_accepts_three(self):
        app = WordStudyApp.__new__(WordStudyApp)

        with patch('builtins.input', side_effect=['5', '6', '3']):
            choice = app._prompt_choice()

        self.assertEqual(choice, '3')

    def test_replay_prompt_rejects_five(self):
        app = WordStudyApp.__new__(WordStudyApp)

        with patch('builtins.input', side_effect=['5', '9']):
            choice = app._prompt_choice_with_replay()

        self.assertEqual(choice, '9')


class QuizOptionTests(unittest.TestCase):
    def setUp(self):
        self.app = WordStudyApp.__new__(WordStudyApp)
        self.normal_options = [
            {'interpretation': '甲', 'vocabulary': 'alpha', 'is_correct': True},
            {'interpretation': '乙', 'vocabulary': 'beta', 'is_correct': False},
            {'interpretation': '丙', 'vocabulary': 'gamma', 'is_correct': False},
        ]
        self.word = {'interpretation': '甲', 'vocabulary': 'alpha'}
        self.build_kwargs = None

    def _build(self, word, kind, include_correct=True):
        self.build_kwargs = {'include_correct': include_correct}
        options = self.normal_options
        if include_correct:
            return options
        return [dict(option, is_correct=False) for option in options]

    def _answer(self, choice, correct_slot):
        with patch.object(self.app, 'build_quiz_options', side_effect=self._build), \
                patch('word_study.random.randint', return_value=correct_slot), \
                patch('builtins.input', return_value=choice):
            return self.app._ask_quiz(self.word, 'interpretation')

    def test_slot_four_makes_none_of_above_correct(self):
        result = self._answer('4', correct_slot=4)
        self.assertTrue(result['correct'])
        self.assertEqual(result['correct_label'], '4')
        self.assertIn(self.word['interpretation'], result['correct_text'])
        self.assertFalse(self.build_kwargs['include_correct'])

    def test_slots_one_to_three_include_the_answer(self):
        text = self.normal_options[0]['interpretation']
        for slot in (1, 2, 3):
            result = self._answer('1', correct_slot=slot)
            self.assertTrue(result['correct'])
            self.assertEqual(result['correct_text'], text)
            self.assertTrue(self.build_kwargs['include_correct'])

    def test_five_is_no_longer_a_valid_choice(self):
        # 第 5 个选项已移除：输入 5 会提示无效，直到输入 1-4 才作答
        with patch.object(self.app, 'build_quiz_options', side_effect=self._build), \
                patch('word_study.random.randint', return_value=1), \
                patch('builtins.input', side_effect=['5', '1']):
            result = self.app._ask_quiz(self.word, 'interpretation')

        self.assertTrue(result['correct'])

    def test_four_options_and_prompt_spacing(self):
        with patch.object(self.app, 'build_quiz_options', side_effect=self._build), \
                patch('word_study.random.randint', return_value=1), \
                patch('builtins.print') as printed, \
                patch('builtins.input', return_value='1') as mocked_input:
            self.app._ask_quiz(self.word, 'interpretation')

        lines = [call.args[0] if call.args else '' for call in printed.call_args_list]
        self.assertIn('  4. 以上三个都不对', lines)
        self.assertNotIn('  5. 不确定', lines)
        # 选项 4 后只有一个空行，和每个选项之间的间距一致
        self.assertEqual(lines[lines.index('  4. 以上三个都不对') + 1], '')
        # 提示本身不再额外插入空行
        self.assertFalse(mocked_input.call_args.args[0].startswith('\n'))

    def _printed_lines(self, choice, correct_slot):
        """跑一次选择题并返回全部打印行"""
        with patch.object(self.app, 'build_quiz_options', side_effect=self._build), \
                patch('word_study.random.randint', return_value=correct_slot), \
                patch('builtins.input', return_value=choice), \
                patch('builtins.print') as printed:
            self.app._ask_quiz(self.word, 'interpretation')
        return [str(call.args[0]) if call.args else ''
                for call in printed.call_args_list]

    def test_wrong_answer_shows_error_icon(self):
        lines = self._printed_lines('2', correct_slot=1)
        self.assertTrue(any(WRONG_ICON in line and '正确答案是' in line
                            for line in lines))

    def test_correct_answer_shows_no_icon(self):
        lines = self._printed_lines('1', correct_slot=1)
        self.assertTrue(any('正确! 答案: ' in line for line in lines))
        self.assertFalse(any(_has_icon(line) for line in lines))

    def test_slot_four_wrong_options_score_zero(self):
        for choice in ('1', '2', '3'):
            self.assertFalse(self._answer(choice, correct_slot=4)['correct'])


class SpellingResultTests(unittest.TestCase):
    """功能3 拼写模式：主模式与错题本/自由练习两套实现都要显示错误图标"""

    def setUp(self):
        self.app = WordStudyApp.__new__(WordStudyApp)
        self.app.study_count = 1
        self.word = {
            'vc_id': 'w1', 'vocabulary': 'alpha',
            'interpretation': '甲', 'phonetic_us': '',
        }

    def _run(self, answer, with_words=False):
        patches = [
            patch.object(self.app, 'get_today_review_words',
                         return_value=[self.word]),
            patch.object(self.app, 'speak'),
            patch.object(self.app, 'record_review'),
            patch.object(self.app, '_print_extra_hint'),
            patch.object(self.app, '_normalize_word', side_effect=lambda w: w),
            patch.object(self.app, 'is_card_due', return_value=True),
            patch('builtins.input', return_value=answer),
        ]
        with patches[0], patches[1], patches[2], patches[3], \
                patches[4], patches[5], patches[6], \
                patch('builtins.print') as printed:
            if with_words:
                self.app.mode_chinese_to_spelling_with_words([self.word])
            else:
                self.app.mode_chinese_to_spelling()
        return [str(call.args[0]) if call.args else ''
                for call in printed.call_args_list]

    def test_wrong_spelling_shows_error_icon_in_both_modes(self):
        for with_words in (False, True):
            lines = self._run('beta', with_words=with_words)
            self.assertTrue(any(WRONG_ICON in line and '拼写错误' in line
                                for line in lines))

    def test_correct_spelling_shows_no_icon_in_both_modes(self):
        for with_words in (False, True):
            lines = self._run('alpha', with_words=with_words)
            self.assertTrue(any('拼写正确!' in line for line in lines))
            self.assertFalse(any(_has_icon(line) for line in lines))


if __name__ == '__main__':
    unittest.main()
