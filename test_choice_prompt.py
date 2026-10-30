import sys
import types
import unittest
from unittest.mock import patch

sys.modules.setdefault('edge_tts', types.SimpleNamespace())

from word_study import WordStudyApp


class ChoicePromptTests(unittest.TestCase):
    def test_prompt_retries_until_valid_choice(self):
        app = WordStudyApp.__new__(WordStudyApp)

        with patch('builtins.input', side_effect=['invalid', '5']):
            choice = app._prompt_choice()

        self.assertEqual(choice, '5')

    def test_prompt_rejects_six_and_accepts_three(self):
        app = WordStudyApp.__new__(WordStudyApp)

        with patch('builtins.input', side_effect=['6', '3']):
            choice = app._prompt_choice()

        self.assertEqual(choice, '3')


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

    def test_five_is_always_wrong(self):
        for slot in (1, 2, 3, 4):
            self.assertFalse(self._answer('5', correct_slot=slot)['correct'])

    def test_slot_four_wrong_options_score_zero(self):
        for choice in ('1', '2', '3'):
            self.assertFalse(self._answer(choice, correct_slot=4)['correct'])


if __name__ == '__main__':
    unittest.main()
