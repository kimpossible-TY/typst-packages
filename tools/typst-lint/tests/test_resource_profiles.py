"""Pure historical profile/repertoire rules, with no native resource reads."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import resource_profiles as profiles

ASCII = profiles.ASCII_RESOURCE_PROFILE
KOREAN = profiles.APPLEGOTHIC_RESOURCE_PROFILE
PUNCTUATION = '·–—‘’“”…₩、。〈〉《》「」『』【】'


class ResourceProfileTests(unittest.TestCase):
    def test_known_names_are_exact_pure_and_historical(self):
        with mock.patch('builtins.open', side_effect=AssertionError('no resource access')):
            for name in (ASCII, KOREAN):
                self.assertEqual(profiles.validate_resource_profile(name), name)
                profiles.validate_source_repertoire(name, b'ASCII $alpha_(1)$')
        self.assertEqual(profiles.KNOWN_RESOURCE_PROFILES, frozenset((ASCII, KOREAN)))
        self.assertEqual(profiles.resource_profile_protected_paths(ASCII), ())
        self.assertEqual(profiles.resource_profile_protected_paths(KOREAN),
            (Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf'),))
        with self.assertRaises(TypeError):
            profiles.REPERTOIRE_VERSIONS[KOREAN] = 'changed'
        for name in (None, False, [], {}, '', 'closed-core-v1', KOREAN.upper(), KOREAN + ' '):
            with self.subTest(name=name), self.assertRaises(ValueError):
                profiles.validate_resource_profile(name)

    def test_ascii_bytes_and_one_optional_bom_are_unchanged(self):
        for profile in (ASCII, KOREAN):
            for source in (b'', bytes(range(128)), b'\xef\xbb\xbf', b'\xef\xbb\xbfASCII'):
                profiles.validate_source_repertoire(profile, source)
            for source in (b'\xef\xbb\xbf\xef\xbb\xbf', b'x\xef\xbb\xbf', b'\xff', b'\xc0\x80'):
                with self.subTest(profile=profile, source=source), self.assertRaises(ValueError):
                    profiles.validate_source_repertoire(profile, source)
        for source in ('한글', 'ㄱㅏ', PUNCTUATION, 'α', '\u00a0'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                profiles.validate_source_repertoire(ASCII, source.encode())

    def test_exact_modern_syllables_jamo_and_punctuation(self):
        self.assertEqual(len(PUNCTUATION), 21)
        syllables = ''.join(map(chr, range(0xAC00, 0xD7A4)))
        jamo = ''.join(map(chr, range(0x3131, 0x3164)))
        self.assertEqual((len(syllables), len(jamo)), (11172, 51))
        source = (syllables + jamo + PUNCTUATION + ' $alpha_(한) + x^(글)$').encode()
        profiles.validate_source_repertoire(KOREAN, source)
        profiles.validate_source_repertoire(KOREAN, b'\xef\xbb\xbf' + source)

    def test_rejects_boundary_neighbors_and_every_other_nonascii_class(self):
        rejected = ('\u0080\u00a0\u00a8\u00b0\u00b2\u0301απΩ∑∫∞'
                    '\u1100\u1112\u115f\u1160\u1161\u1175\u11a8\u11c2'
                    '\u312f\u3130\u3164\u3165\u318e\u318f'
                    '\ua960\ua97c\uabff\ud7a4\ud7af\ud7b0\ud7c6\ud7cb\ud7fb'
                    '\u200b\u200c\u200d\u202a\u202e\u2066\u2069\ufe0f\ufeff'
                    '\ue000\uf8ff\u0378😀🧑\U000e0100\U0010ffff')
        for character in rejected:
            with self.subTest(codepoint=f'U+{ord(character):04X}'), self.assertRaises(ValueError):
                profiles.validate_source_repertoire(KOREAN, b'x' + character.encode())
        # NFD must refuse; no normalization is performed before repertoire checks.
        for source in ('한글', 'e\u0301', '가\u0301', '한\ufe0f', '한\u200d글'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                profiles.validate_source_repertoire(KOREAN, source.encode())
        for source in ('한글', bytearray(b'ASCII'), memoryview(b'ASCII'), None):
            with self.subTest(source=source), self.assertRaises(ValueError):
                profiles.validate_source_repertoire(KOREAN, source)


if __name__ == '__main__':
    unittest.main()
