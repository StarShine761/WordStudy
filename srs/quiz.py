# -*- coding: utf-8 -*-
"""Answer options for the four quiz modes.

The correct word is always one of the four options; distractors are drawn from
the vocabulary database and biased towards the same part of speech, a similar
difficulty level and a similar length.
"""

import random
import re

_POS_ALIASES = {
    "vt": "v", "vi": "v", "v": "v",
    "n": "n", "adj": "adj", "adv": "adv",
    "prep": "prep", "conj": "conj", "pron": "pron",
    "num": "num", "art": "art", "int": "int", "interj": "int",
    "aux": "aux", "modal": "aux",
}
_POS_PATTERN = re.compile(
    r"(?:^|[\s\n])(vt|vi|v|n|adj|adv|prep|conj|pron|num|art|int|interj|aux|modal)\.",
    re.IGNORECASE,
)

_CANDIDATE_SQL = """
SELECT vc_id, vc_vocabulary, vc_interpretation, vc_phonetic_us, vc_difficulty
FROM VOC_TB
WHERE vc_id != ? AND vc_vocabulary IS NOT NULL AND vc_vocabulary != ''
  AND vc_interpretation IS NOT NULL AND vc_interpretation != ''
ORDER BY RANDOM()
LIMIT ?
"""


def _normalize(text):
    return " ".join(str(text or "").split()).lower()


def _part_of_speech(interpretation):
    match = _POS_PATTERN.search((interpretation or "")[:80])
    if not match:
        return ""
    return _POS_ALIASES.get(match.group(1).lower(), "")


def _candidate_score(candidate, correct_pos, correct_difficulty, correct_length):
    score = random.random() * 0.5
    if correct_pos and candidate["pos"] == correct_pos:
        score += 4.0
    if correct_difficulty > 0 and candidate["difficulty"] > 0:
        score += max(0.0, 2.0 - abs(candidate["difficulty"] - correct_difficulty))
    score += max(0.0, 1.0 - abs(len(candidate["vocabulary"]) - correct_length) / 6.0)
    return score


def build_options(conn, correct, kind="interpretation", include_correct=True):
    """Return three normal options (UI labels 1-3) for a quiz question.

    include_correct=True: one correct option plus two distractors.
    include_correct=False: three distractors only, used when the correct
    answer is the UI's option 4 ("以上三个都不对").

    kind="interpretation" shows Chinese meanings; kind="vocabulary" shows the
    English words. Each option carries is_correct for label lookup.
    """
    correct_id = str(correct.get("vc_id") or correct.get("voc_id") or "")
    correct_word = correct.get("vocabulary") or ""
    correct_meaning = correct.get("interpretation") or ""
    correct_pos = _part_of_speech(correct_meaning)
    correct_difficulty = float(correct.get("difficulty") or 0)
    correct_length = len(correct_word)

    correct_option = {
        "vc_id": correct_id,
        "voc_id": correct_id,
        "vocabulary": correct_word,
        "interpretation": correct_meaning,
        "phonetic_us": correct.get("phonetic_us") or "",
        "is_correct": True,
    }

    rows = conn.execute(_CANDIDATE_SQL, (correct_id, 600)).fetchall()
    seen_meanings = {_normalize(correct_meaning)}
    seen_words = {_normalize(correct_word)}
    seen_ids = {correct_id}

    candidates = []
    for row in rows:
        vc_id, vocabulary, interpretation, phonetic_us, difficulty = row
        vc_id = str(vc_id)
        word_key = _normalize(vocabulary)
        meaning_key = _normalize(interpretation)
        if vc_id in seen_ids or word_key in seen_words:
            continue
        if kind == "interpretation" and meaning_key in seen_meanings:
            continue
        candidates.append({
            "vc_id": vc_id,
            "voc_id": vc_id,
            "vocabulary": vocabulary,
            "interpretation": interpretation,
            "phonetic_us": phonetic_us or "",
            "difficulty": float(difficulty or 0),
            "pos": _part_of_speech(interpretation),
            "is_correct": False,
            "_word_key": word_key,
            "_meaning_key": meaning_key,
        })

    candidates.sort(
        key=lambda item: _candidate_score(
            item, correct_pos, correct_difficulty, correct_length
        ),
        reverse=True,
    )

    distractor_count = 2 if include_correct else 3

    picked = []
    for candidate in candidates[: max(distractor_count * 4, distractor_count)]:
        if len(picked) >= distractor_count:
            break
        if candidate["_word_key"] in seen_words:
            continue
        if kind == "interpretation" and candidate["_meaning_key"] in seen_meanings:
            continue
        seen_words.add(candidate["_word_key"])
        seen_meanings.add(candidate["_meaning_key"])
        picked.append(candidate)

    options = ([correct_option] if include_correct else []) + picked
    for option in options:
        option.pop("_word_key", None)
        option.pop("_meaning_key", None)
        option.pop("difficulty", None)
        option.pop("pos", None)
    random.shuffle(options)
    return options
