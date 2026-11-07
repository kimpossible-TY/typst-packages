from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capitalization import BodyKind, Context, MarkerBinding, StartKind, WORDS, evaluate
from edit_plan import Analysis, Edit, converge, plan
from typst_lint import InputError, digest, load_rules, read_config


class CapitalizationTests(unittest.TestCase):
    def fixture(self, word=b'this', suffix=b' holds.\r\n', prefix=b'\xef\xbb\xbf// unchanged comment\r\n', language='en'):
        source = prefix + b'#paragraph_tab\r\n' + word + suffix
        ms = len(prefix)
        start = ms + len(b'#paragraph_tab\r\n')
        ctx = Context(digest(source), 'chapter.typ', len(prefix), len(source), ms,
                      ms + len(b'#paragraph_tab'), start, start + len(word), word,
                      StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                      MarkerBinding.PARAGRAPH_TAB, language)
        return source, ctx

    def test_exact_one_byte_edit_and_idempotence(self):
        source, ctx = self.fixture()
        result = evaluate(source, ctx)
        self.assertIsNone(result.skip_reason)
        self.assertEqual(result.edit, Edit(ctx.first_start, ctx.first_start + 1, b'T'))
        self.assertEqual((result.diagnostic.rule_id, result.diagnostic.grade), ('pde.paragraph-capitalization', 'warning'))
        self.assertEqual((result.diagnostic.line, result.diagnostic.column), (3, 1))
        candidate = plan(source, [result.edit]).candidate()
        self.assertEqual(candidate, source[:ctx.first_start] + b'T' + source[ctx.first_start + 1:])
        again = evaluate(candidate, replace(ctx, source_sha256=digest(candidate), expected_first=b'This'))
        self.assertEqual(again.skip_reason, 'already-uppercase')
        self.assertIsNone(again.edit)

    def test_exact_allowlist_and_case(self):
        self.assertEqual(WORDS, frozenset(b'an the this these that those we it let assume suppose consider define denote hence thus therefore since if for when then now note recall observe'.split()))
        for word in WORDS:
            with self.subTest(word=word):
                source, ctx = self.fixture(word)
                self.assertIsNotNone(evaluate(source, ctx).edit)
                for upper in (word.capitalize(), word.upper()):
                    source, ctx = self.fixture(upper)
                    self.assertEqual(evaluate(source, ctx).skip_reason, 'already-uppercase')

    def test_excluded_words_and_boundaries(self):
        for word in (b'a', b'x', b'iPhone', b'eBay', b'tHis', b'theorem', b'theseus',
                     b'this-like', b"it's", b'"this', b'\\this', b'@this', b'#this',
                     'élan'.encode(), 'α'.encode(), b'THIs'):
            with self.subTest(word=word):
                source, ctx = self.fixture(word)
                result = evaluate(source, ctx)
                self.assertIsNone(result.edit)
                self.assertIsNotNone(result.skip_reason)
        for suffix in (b'', b'.', b'-like ', b"'s ", b'ness ', '\u00a0holds'.encode()):
            source, ctx = self.fixture(b'this', suffix)
            self.assertIsNone(evaluate(source, ctx).edit)
        for whitespace in (b' ', b'\t', b'\n', b'\r\n'):
            source, ctx = self.fixture(b'this', whitespace + b'holds')
            self.assertIsNotNone(evaluate(source, ctx).edit)

    def test_all_unsupported_start_kinds_no_forward_search(self):
        source, ctx = self.fixture(b'this', b' $x$ holds.')
        self.assertIsNotNone(evaluate(source, ctx).edit)  # Later math is fine.
        for kind in StartKind:
            if kind is not StartKind.LITERAL_WORD:
                self.assertEqual(evaluate(source, replace(ctx, start_kind=kind)).skip_reason,
                                 'first-token-not-direct-literal-word')
        # A stale/misclassified adapter trying to skip initial math is refused.
        initial = source[:ctx.first_start] + b'$x$ ' + source[ctx.first_start:]
        shifted = replace(ctx, source_sha256=digest(initial), body_end=len(initial),
                          first_start=ctx.first_start+4, first_end=ctx.first_end+4)
        self.assertEqual(evaluate(initial, shifted).skip_reason, 'unsupported-content-before-first-word')

    def test_language_and_suppression(self):
        for language in (None, '', 'unknown', 'dynamic', 'ko', 'fr', 'English', ' en ', 'en_US', 'en-', 'en-x', 'en-123456789', 'en-US-', 'en--US', 'en-ÜS', 'en-US\n'):
            source, ctx = self.fixture(language=language)
            self.assertEqual(evaluate(source, ctx).skip_reason, 'effective-language-not-explicit-english')
        for language in ('en', 'EN', 'en-US', 'en-GB', 'En-latn-US', 'en-12', 'en-12345678'):
            source, ctx = self.fixture(language=language)
            self.assertIsNotNone(evaluate(source, ctx).edit)
        source, ctx = self.fixture()
        self.assertEqual(evaluate(source, replace(ctx, suppressed=True)).skip_reason, 'suppressed')

    def test_invalid_stale_or_unproven_contexts(self):
        source, ctx = self.fixture()
        changes = [dict(source_sha256='stale'), dict(expected_first=b'that'), dict(first_start=-1),
                   dict(first_end=len(source)+1), dict(body_end=ctx.first_end-1), dict(first_start=True),
                   dict(first_start=ctx.first_start+1), dict(marker_end=ctx.marker_end-1),
                   dict(start_kind='literal-word'), dict(suppressed=1), dict(effective_language=1),
                   dict(path='../escape.typ'), dict(body_start=1)]
        for change in changes:
            with self.subTest(change=change):
                result = evaluate(source, replace(ctx, **change))
                self.assertIsNone(result.edit)
                self.assertIsNotNone(result.skip_reason)
        for binding in (MarkerBinding.OTHER, MarkerBinding.UNKNOWN):
            self.assertEqual(evaluate(source, replace(ctx, marker_binding=binding)).skip_reason,
                             'unresolved-or-unrelated-marker')
        for body in (BodyKind.OTHER, BodyKind.UNKNOWN):
            self.assertEqual(evaluate(source, replace(ctx, body_kind=body)).skip_reason, 'unsupported-body-context')
        self.assertIsNotNone(evaluate(bytearray(source), ctx).skip_reason)
        self.assertIsNotNone(evaluate(source, {}).skip_reason)
        bad = source + b'\xff'
        self.assertEqual(evaluate(bad, replace(ctx, source_sha256=digest(bad))).skip_reason, 'invalid-path-or-utf8')

    def test_comments_wrappers_and_nonstandalone_marker_refused(self):
        source, ctx = self.fixture()
        for inserted in (b'// comment\n', b'#emph[', b'"', b'#other() ', b'$x$ '):
            modified = source[:ctx.first_start] + inserted + source[ctx.first_start:]
            shifted = replace(ctx, source_sha256=digest(modified), body_end=len(modified),
                              first_start=ctx.first_start+len(inserted), first_end=ctx.first_end+len(inserted))
            self.assertIsNone(evaluate(modified, shifted).edit)
        source, ctx = self.fixture(prefix=b'prose ')
        self.assertEqual(evaluate(source, replace(ctx, body_start=0)).skip_reason, 'marker-not-standalone')

    def test_marker_requires_adjacent_prose_line(self):
        source, ctx = self.fixture()
        for gap, eligible in ((b'\n', True), (b'\r\n', True),
                              (b' \t\n\t ', True), (b'\t\r\n  ', True),
                              (b'\n\n', False), (b'\r\n\r\n', False),
                              (b'\n \t\n', False), (b'\r', False),
                              (b' \r\t', False), (b'\r\r\n', False),
                              (b'\n\r', False), (b' ', False)):
            with self.subTest(gap=gap):
                modified = source[:ctx.marker_end] + gap + source[ctx.first_start:]
                start = ctx.marker_end + len(gap)
                context = replace(ctx, source_sha256=digest(modified), body_end=len(modified),
                                  first_start=start, first_end=start+len(ctx.expected_first))
                result = evaluate(modified, context)
                self.assertEqual(result.edit is not None, eligible)
                if not eligible:
                    self.assertEqual(result.skip_reason, 'unsupported-content-before-first-word')

    def test_marker_insertion_then_uppercase_simulated_convergence(self):
        # Synthetic adapter only: this is not parsing arbitrary Typst source.
        original = b'First.\n\nthis holds.\n'
        marker = b'#paragraph_tab\n'
        prefix = b'First.\n\n'
        def analyze(data):
            if data == original:
                return Analysis(True, True, frozenset({'marker', 'capitalization'}),
                                (Edit(len(prefix), len(prefix), marker),))
            start = len(prefix) + len(marker)
            word = data[start:start+4]
            ctx = Context(digest(data), 'fixture.typ', len(prefix), len(data), len(prefix),
                          len(prefix)+len(marker)-1, start, start+4, word,
                          StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                          MarkerBinding.PARAGRAPH_TAB, 'en')
            result = evaluate(data, ctx)
            return Analysis(True, True, frozenset({'capitalization'}) if result.edit else frozenset(),
                            (result.edit,) if result.edit else ())
        candidate = converge(original, analyze)
        self.assertEqual(candidate.content, prefix + marker + b'This holds.\n')
        self.assertEqual(candidate.passes, 2)
        self.assertEqual(converge(candidate.content, analyze).passes, 0)

    def test_capitalization_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / '.typst-lint.toml'
            rules = load_rules()[0]['rules']
            self.assertIsNone(read_config(root, rules).capitalization_language)
            for value in ('en', 'en-US', 'ko'):
                config.write_text('[capitalization]\nlanguage="' + value + '"')
                self.assertEqual(read_config(root, rules).capitalization_language, value)
            for text in ('capitalization="en"', 'capitalization=[]', '[capitalization]\nlanguage=1',
                         '[capitalization]\nlanguage=[]', '[capitalization]\nlanguage=" "',
                         '[capitalization]\nunknown="en"'):
                config.write_text(text)
                with self.subTest(text=text), self.assertRaises(InputError):
                    read_config(root, rules)


if __name__ == '__main__':
    unittest.main()
