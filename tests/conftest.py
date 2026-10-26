from __future__ import annotations
from collections import defaultdict
from shiju.domain import RhymeMode
import os
import uuid
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, quote
import pytest

class FakeLexicon:

    def __init__(self):
        self.tones = {'山': ['平'], '春': ['平'], '风': ['平'], '花': ['平'], '雨': ['仄'], '月': ['仄'], '夜': ['仄'], '海': ['仄'], '的': ['平'], '不': ['仄']}
        self.parts = {'山': ['一'], '春': ['一'], '风': ['二'], '花': ['三'], '雨': ['四'], '月': ['四'], '夜': ['五'], '海': ['五'], '的': ['一'], '不': ['四']}

    def get_pingze(self, char):
        return list(self.tones.get(char, ()))

    def get_rhyme_part(self, char):
        return list(self.parts.get(char, ()))

    def get_rhyme_part_by_tone(self, char, tone):
        return self.get_rhyme_part(char) if tone in self.get_pingze(char) else []

class FakeTokenizer:

    def __init__(self, mapping=None):
        defaults = {0: '<eos>', 1: '[content]', 2: '山', 3: '雨', 4: '，', 5: '。', 6: '\n', 7: '、', 8: '；', 9: '春', 10: '风', 11: '花', 12: '的', 13: '不', 14: '？', 15: '！', 16: ',', 17: '.', 18: '?', 19: '!'}
        if mapping:
            defaults.update(mapping)
        self.mapping = defaults
        self.reverse = {text: token_id for token_id, text in self.mapping.items()}
        self.eos_token_id = 0

    def get_vocab(self):
        return {text: token_id for token_id, text in self.mapping.items()}

    def decode(self, token_ids, **kwargs):
        if hasattr(token_ids, 'tolist'):
            token_ids = token_ids.tolist()
        return ''.join((self.mapping.get(int(token_id), '') for token_id in token_ids))

    def encode(self, text, **kwargs):
        if text in self.reverse:
            return [self.reverse[text]]
        result = []
        cursor = 0
        values = sorted(self.reverse, key=len, reverse=True)
        while cursor < len(text):
            match = next((item for item in values if text.startswith(item, cursor)), None)
            if match is None:
                cursor += 1
                continue
            result.append(self.reverse[match])
            cursor += len(match)
        return result

class FakeVocab:

    def __init__(self, tokenizer, lexicon):
        self.tokenizer = tokenizer
        self.lexicon = lexicon
        self._texts = {token_id: text for token_id, text in tokenizer.mapping.items() if text and all((char in lexicon.tones for char in text))}

    def text_for_token(self, token_id):
        return self._texts.get(token_id, '')

    def common_bigrams(self):
        return frozenset((text for text in self._texts.values() if len(text) == 2))

    def resolve_patterns(self, patterns, ignore_rhyme=False, strict_polyphonic=True):
        result = set()
        for pattern in patterns:
            for token_id, text in self._texts.items():
                if len(text) != pattern.length:
                    continue
                token_tones = ''.join((self.lexicon.get_pingze(char)[0] for char in text))
                if token_tones != pattern.tones:
                    continue
                rhyme = pattern.rhyme
                if not ignore_rhyme and rhyme.mode is not RhymeMode.NONE:
                    parts = set(self.lexicon.get_rhyme_part_by_tone(text[-1], rhyme.tone))
                    if rhyme.mode is RhymeMode.ANY:
                        if not parts or parts.intersection(rhyme.excluded_parts):
                            continue
                    elif not parts.intersection(rhyme.parts):
                        continue
                result.add(token_id)
        return result
