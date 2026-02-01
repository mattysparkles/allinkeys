from __future__ import annotations

import hashlib
from typing import Sequence

from utils.word_lists import ADJECTIVES, NOUNS, VERBS


def _select_word(words: Sequence[str], seed: int) -> str:
    if not words:
        return "Machine"
    index = seed % len(words)
    return words[index]


def generate_machine_name(machine_id: str) -> str:
    digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    pattern = digest[0] & 1 if digest else 0
    if pattern == 0:
        adjective_seed = digest[1] if len(digest) > 1 else 0
        noun_seed = int.from_bytes(digest[2:4], "big") if len(digest) >= 4 else digest[2] if len(digest) > 2 else 0
        adjective = _select_word(ADJECTIVES, adjective_seed)
        noun = _select_word(NOUNS, noun_seed)
        return f"{adjective}-{noun}"
    noun_seed = int.from_bytes(digest[1:3], "big") if len(digest) >= 3 else digest[1] if len(digest) > 1 else 0
    verb_seed = int.from_bytes(digest[3:5], "big") if len(digest) >= 5 else digest[3] if len(digest) > 3 else 0
    noun = _select_word(NOUNS, noun_seed)
    verb = _select_word(VERBS, verb_seed)
    return f"{noun}-{verb}"
