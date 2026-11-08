"""Real parser/package/CLI evidence for bounded footnote paragraph repairs.

Only temporary consumers and exact test-owned package snapshots are written.
The first marker pass must own the complete boundary policy; capitalization is
proved by a subsequent real analysis, never fabricated as a footnote repair.
"""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from audit_rules import candidate_verdict
from edit_plan import Edit, plan
from footnote_rules import GAP
from package_loader import load_packages
from paragraph_rules import CAPITALIZATION
from prose_marker_rules import RULE_ID as MARKER, analyze as markers
from source_analysis import analyze
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, report

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
SHOW = b'#show: apply-paragraph-tabs\n'
LANG = b'#set text(lang: "en")\n'
PREFIX = IMPORT + SHOW + LANG
NOTE = b'First ends.#footnote[Reason.]\n\n'
MARKED = NOTE + b'#paragraph_tab\nThis holds.\n'
UNMARKED = NOTE + b'this consequence follows.\n'
SCRIPT = 'core.parenthesized-scripts'


def proposed(source, value):
    return plan(source, (Edit(e['start'], e['end'], e['replacement'].encode('utf-8'))
                         for e in value['proposed_edits'])).candidate()


def owners(value):
    return {rid for edit in value['proposed_edits']
            for rid in edit.get('rule_ids', [edit['rule_id']])}


def explicit_break(source, newline=b'\n'):
    boundary = b']' + newline + newline
    return source.replace(boundary, b']' + newline + b'#parbreak()' + newline + newline, 1)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class FootnoteParagraphIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory(prefix='footnote-package-snapshot-')
        cls.addClassCleanup(cls.package_temp.cleanup)
        cls.package_root = Path(cls.package_temp.name).resolve()
        cls.package_bytes = {}
        for name in ('typst.toml', 'lib.typ'):
            relative = Path('local/text-utils/0.1.2') / name
            raw = ((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes()
            target = cls.package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            cls.package_bytes[relative] = raw
        loaded = load_packages(cls.package_root, (TEXT_UTILS,))
        cls.packages = loaded.sources
        assert not loaded.unavailable and len(cls.packages) == 1
        assert digest(cls.packages[0].source) == TEXT_UTILS_SOURCE_SHA256

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='footnote-paragraph-consumer-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / 'main.typ'
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(b'rule-set="pde"\ninclude=["main.typ"]\n')

    def tearDown(self):
        for relative, raw in self.package_bytes.items():
            self.assertEqual((self.package_root / relative).read_bytes(), raw)
            self.assertEqual(((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes(), raw)

    def results(self, source, *, packages=None, ignored=frozenset(), language=None, pack='pde'):
        return analyze(parse(source), 'main.typ', pack=pack,
                       packages=self.packages if packages is None else packages,
                       ignored=ignored, fallback_language=language)

    def candidate(self, source, results):
        result = plan(source, (edit for value in results.values() for edit in value.edits))
        self.assertFalse(result.deferred)
        return result.candidate()

    def pair(self, source):
        self.target.write_bytes(source)
        contexts = {}
        before = report(self.root, 'dry-run', package_path=self.package_root,
                        _analysis_observer=contexts.__setitem__)
        candidate = proposed(source, before)
        self.assertEqual(self.target.read_bytes(), source)
        self.target.write_bytes(candidate)  # Only this test-owned consumer.
        after = report(self.root, 'check', package_path=self.package_root)
        return before, after, candidate, contexts

    def assert_verified(self, source, before, after, candidate, contexts):
        verdict = candidate_verdict(before, after, {'main.typ': source.decode('utf-8')},
                                    {'main.typ': candidate.decode('utf-8')},
                                    analysis_contexts=contexts)
        self.assertTrue(verdict['verified'], verdict)
        self.assertFalse(after['proposed_edits'])
        self.assertTrue(all(e['baseline_sha256'] == digest(source)
                            for e in before['proposed_edits']))
        self.assertFalse(before['compiler_invoked'] or before['applied_fixes']
                         or before['coverage']['complete'])
        return verdict

    def test_existing_marker_has_only_gap_owned_next_line_insertion(self):
        source = PREFIX + MARKED
        results = self.results(source)
        at = source.index(b']\n\n') + 2
        self.assertEqual(results[GAP].edits, (Edit(at, at, b'#parbreak()\n'),))
        self.assertFalse(results[MARKER].diagnostics or results[MARKER].edits)
        self.assertEqual(self.candidate(source, results), explicit_break(source))
        before, after, candidate, contexts = self.pair(source)
        self.assertEqual(candidate, explicit_break(source))
        self.assertEqual(owners(before), {GAP})
        self.assert_verified(source, before, after, candidate, contexts)
        self.assertFalse(after['diagnostics'])
        self.assertEqual(self.candidate(candidate, self.results(candidate)), candidate)

    def test_missing_marker_raw_pass_owns_both_insertions_and_keeps_lowercase(self):
        source = PREFIX + UNMARKED
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages)
        first = markers(tree, 'main.typ', resolution, allow_followup_casing=True)
        break_at = source.index(b']\n\n') + 2
        marker_at = source.index(b'this consequence')
        self.assertEqual(first.edits, (Edit(break_at, break_at, b'#parbreak()\n'),
                                       Edit(marker_at, marker_at, b'#paragraph_tab\n')))
        inserted = plan(source, first.edits).candidate()
        self.assertEqual(inserted, explicit_break(source).replace(
            b'this consequence', b'#paragraph_tab\nthis consequence'))
        self.assertIn(b'\nthis consequence', inserted)
        raw = analyze(tree, 'main.typ', pack='pde', packages=self.packages, _verify=False)
        self.assertTrue(raw[GAP].diagnostics)
        self.assertFalse(raw[GAP].edits)
        second = self.results(inserted)
        self.assertEqual({rid for rid, value in second.items() if value.edits}, {CAPITALIZATION})
        self.assertFalse(second[GAP].diagnostics or second[MARKER].diagnostics)

    def test_lowercase_candidate_has_authentic_two_pass_history_and_audit_context(self):
        source = PREFIX + UNMARKED
        results = self.results(source)
        expected = explicit_break(source).replace(b'this consequence', b'#paragraph_tab\nThis consequence')
        self.assertEqual(self.candidate(source, results), expected)
        self.assertFalse(results[GAP].edits)
        first, second = results[MARKER].repair_steps
        self.assertEqual(first.before, source)
        self.assertEqual(first.after, second.before)
        self.assertEqual(second.after, expected)
        self.assertEqual({rid for e in first.applied for rid in e.rule_ids}, {MARKER})
        self.assertEqual({rid for e in second.applied for rid in e.rule_ids}, {CAPITALIZATION})
        self.assertTrue(all(e.edit.start == e.edit.end for e in first.applied))
        self.assertEqual(len(first.applied), 2)
        self.assertEqual(len(second.applied), 1)
        self.assertEqual(second.applied[0].edit.replacement, b'T')
        self.assertEqual(second.applied[0].edit.end - second.applied[0].edit.start, 1)
        self.assertEqual(plan(first.before, first.edits).candidate(), first.after)
        self.assertEqual(plan(second.before, second.edits).candidate(), second.after)
        before, after, candidate, contexts = self.pair(source)
        self.assertEqual(candidate, expected)
        self.assertEqual(owners(before), {MARKER, CAPITALIZATION})
        self.assertTrue(all(e['repair_passes'] == 2 for e in before['proposed_edits']))
        record, = before['convergence_evidence']
        self.assertEqual(record['passes'], 2)
        self.assertEqual(record['baseline_sha256'], digest(source))
        self.assertEqual(record['candidate_sha256'], digest(candidate))
        self.assert_verified(source, before, after, candidate, contexts)
        self.assertFalse(after['diagnostics'])

    def test_uppercase_missing_marker_is_one_marker_owned_policy_repair(self):
        source = PREFIX + UNMARKED.replace(b'this consequence', b'This consequence')
        before, after, candidate, contexts = self.pair(source)
        self.assertEqual(candidate, explicit_break(source).replace(
            b'This consequence', b'#paragraph_tab\nThis consequence'))
        self.assertEqual(owners(before), {MARKER})
        self.assertEqual(len(before['proposed_edits']), 2)
        self.assertTrue(all(e['start'] == e['end'] and e.get('repair_passes', 1) == 1
                            for e in before['proposed_edits']))
        self.assert_verified(source, before, after, candidate, contexts)

    def test_reviewed_left_marker_and_multiline_literal_note_keep_complete_paragraph_proof(self):
        source = (PREFIX + b'\n#paragraph_tab\nThis ends.#footnote[Reason.\n\nAnother reason.]\n\n'
                  + b'#paragraph_tab\nThis holds.\n')
        before, after, candidate, contexts = self.pair(source)
        self.assertEqual(candidate, explicit_break(source))
        self.assertEqual(owners(before), {GAP})
        self.assert_verified(source, before, after, candidate, contexts)
        self.assertFalse(after['diagnostics'])

    def test_bom_crlf_unicode_whitespace_and_later_directive_bytes_survive(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline):
                source = (b'\xef\xbb\xbf' + PREFIX.replace(b'\n', newline)
                          + '이전 ends.#footnote[이유.]'.encode() + newline + b' \t' + newline * 2
                          + b' \tthis consequence follows.' + newline * 2
                          + b'// typst-lint: ignore-next core.parenthesized-scripts -- preserve notation'
                          + newline + b'$ z^j $' + newline)
                note_end = source.index('이유.]'.encode()) + len('이유.]'.encode()) + len(newline)
                expected = (source[:note_end] + b'#parbreak()' + newline + source[note_end:])
                expected = expected.replace(b' \tthis', b'#paragraph_tab' + newline + b' \tThis')
                before, after, candidate, contexts = self.pair(source)
                self.assertEqual(candidate, expected)
                self.assertEqual(before['suppressed_by_rule'], {SCRIPT: 1})
                self.assertEqual(before['suppressed_by_rule'], after['suppressed_by_rule'])
                self.assert_verified(source, before, after, candidate, contexts)

    def test_existing_marker_keeps_preexisting_language_and_casing_refusals(self):
        for language, word in ((LANG, b'iPhone'), (LANG, b'Second'),
                               (b'', b'this'), (b'#set text(lang: "ko")\n', b'this'),
                               (b'#set text(lang: "ko")\n', '따라서'.encode())):
            with self.subTest(language=language, word=word):
                source = IMPORT + SHOW + language + NOTE + b'#paragraph_tab\n' + word + b' holds.\n'
                before, after, candidate, contexts = self.pair(source)
                self.assertEqual(candidate, explicit_break(source))
                self.assertEqual(owners(before), {GAP})
                # Independent replay checks surviving diagnostics and shifted
                # refusal reasons, rather than requiring unrelated findings to vanish.
                self.assert_verified(source, before, after, candidate, contexts)

    def test_config_ignores_never_leave_a_half_missing_marker_repair(self):
        source = PREFIX + UNMARKED
        inserted = explicit_break(source).replace(b'this consequence', b'#paragraph_tab\nthis consequence')
        for ignored, expected, expected_owners in (
                ({MARKER}, source, set()),
                ({GAP}, inserted.replace(b'\nthis consequence', b'\nThis consequence'), {MARKER, CAPITALIZATION}),
                ({CAPITALIZATION}, inserted, {MARKER}),
                ({GAP, MARKER}, source, set())):
            with self.subTest(ignored=ignored):
                self.target.write_bytes(source)
                self.config.write_text('rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\n'
                                       + 'rules=' + json.dumps(sorted(ignored)) + '\nreason="intentional"\n')
                value = report(self.root, 'dry-run', package_path=self.package_root)
                self.assertEqual(proposed(source, value), expected)
                self.assertEqual(owners(value), expected_owners)
                self.assertFalse(owners(value) & ignored)
                direct = self.results(source, ignored=frozenset(ignored))
                self.assertEqual(self.candidate(source, direct), expected)
        marked = PREFIX + MARKED
        self.assertEqual(self.candidate(marked, self.results(marked, ignored=frozenset({GAP}))), marked)
        self.assertEqual(self.candidate(source, self.results(source, pack='core')), source)

    def test_inline_directives_are_not_crossed_or_used_to_authorize_half_repairs(self):
        source = (PREFIX + NOTE
                  + b'// typst-lint: ignore-next pde.prose-paragraph-marker -- preserve paragraph\n'
                  + b'this consequence follows.\n')
        self.target.write_bytes(source)
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertEqual(proposed(source, value), source)
        self.assertFalse(value['configuration_failures'])
        # The comment makes this boundary ineligible before marker diagnosis;
        # it must not be counted as a repaired or suppressed marker finding.
        self.assertNotIn(MARKER, value['suppressed_by_rule'])
        self.assertTrue(any(d['rule_id'] == GAP for d in value['diagnostics']))
        self.assertFalse(value['proposed_edits'])
        # A genuine suppressed footnote diagnostic is retained without moving
        # its directive to a manufactured break or marker construct.
        source = (PREFIX + b'First ends.\n'
                  + b'// typst-lint: ignore-next pde.footnote-trailing-gap -- preserve boundary\n'
                  + b'#footnote[Reason.]\n\nthis consequence follows.\n')
        self.target.write_bytes(source)
        value = report(self.root, 'dry-run', package_path=self.package_root)
        self.assertEqual(proposed(source, value), source)
        self.assertFalse(value['configuration_failures'])
        self.assertEqual(value['suppressed_by_rule'].get(GAP), 1)
        self.assertFalse(value['proposed_edits'])

    def test_existing_canonical_break_is_not_duplicated_or_multiple_breaks_collapsed(self):
        for body in (MARKED, UNMARKED):
            source = explicit_break(PREFIX + body)
            with self.subTest(body=body):
                results = self.results(source)
                self.assertFalse(results[GAP].diagnostics or results[GAP].edits)
                candidate = self.candidate(source, results)
                expected = (source if body == MARKED else source.replace(
                    b'this consequence', b'#paragraph_tab\nThis consequence'))
                self.assertEqual(candidate, expected)
                self.assertEqual(candidate.count(b'#parbreak()'), 1)
                if body == UNMARKED:
                    before, after, checked, contexts = self.pair(source)
                    self.assertEqual(checked, expected)
                    self.assertEqual(owners(before), {MARKER, CAPITALIZATION})
                    self.assert_verified(source, before, after, checked, contexts)
            multiple = source.replace(b'#parbreak()\n', b'#parbreak()\n#parbreak()\n')
            self.assertEqual(self.candidate(multiple, self.results(multiple)), multiple)

    def test_canonical_break_preserves_known_state_but_never_restores_unknown_state(self):
        target = b'#paragraph_tab\nthis holds.\n'
        known = PREFIX + b'#parbreak()\n\n' + target
        result = self.results(known)
        self.assertEqual(self.candidate(known, result), known.replace(b'this holds', b'This holds'))
        for expression in (b'#parbreak( )', b'#parbreak(/* trivia */)', b'#(parbreak)()',
                           b'#let br = parbreak\n#br()', b'#block[#parbreak()]',
                           b'text #parbreak() tail.', b'#unknown()\n#parbreak()'):
            with self.subTest(expression=expression):
                source = PREFIX + expression + b'\n\n' + target
                tree = parse(source)
                resolution = resolve(tree, packages=self.packages)
                index = next(i for i in tree.nodes[0].children if tree.nodes[i].kind == 'Text'
                             and source[tree.nodes[i].start:tree.nodes[i].end].startswith(b'this'))
                self.assertNotIn(index, resolution.paragraph_insertion_proof.available)
                self.assertFalse(resolution.facts[index].prose_safe)
                self.assertFalse(self.results(source)[CAPITALIZATION].edits)
        for before in (b'#show: unknown\n', b'#unknown()\n'):
            source = PREFIX + before + b'#parbreak()\n\n' + target
            self.assertEqual(self.candidate(source, self.results(source)), source)

    def test_reviewed_marker_show_and_exact_builtin_break_evidence_are_required(self):
        for prefix in (IMPORT + LANG, IMPORT + b'#show: unknown\n' + SHOW + LANG,
                       IMPORT + b'#show: apply-paragraph-tabs()\n' + LANG,
                       IMPORT + b'#show: apply-paragraph-tabs.with()\n' + LANG,
                       PREFIX + b'#show: unknown\n',
                       IMPORT.replace(b'paragraph_tab,', b'paragraph_tab as tab,') + SHOW + LANG,
                       PREFIX + b'#let paragraph_tab = "other"\n',
                       PREFIX + b'#let parbreak = unknown\n',
                       PREFIX + b'#let parbreak = () => []\n',
                       PREFIX + b'#(parbreak = unknown)\n'):
            for body in (MARKED, UNMARKED):
                with self.subTest(prefix=prefix, body=body):
                    source = prefix + body
                    result = self.results(source)
                    self.assertFalse(result[GAP].edits or result[MARKER].edits)
                    self.assertEqual(self.candidate(source, result), source)
        source = PREFIX + UNMARKED
        for packages in ((), (replace(self.packages[0], source=self.packages[0].source + b'\n'),)):
            with self.subTest(packages=bool(packages)):
                result = self.results(source, packages=packages)
                self.assertFalse(result[GAP].edits or result[MARKER].edits)
                self.assertEqual(self.candidate(source, result), source)

    def test_unmarked_protected_unsupported_and_nonenglish_starts_are_not_half_repaired(self):
        for lang, initial, fallback in ((LANG, b'iPhone', None), (LANG, b'Second', None),
                                        (LANG, '따라서'.encode(), None), (b'', b'this', None),
                                        (b'#set text(lang: "ko")\n', b'this', 'en'),
                                        (b'#set text(lang: variable)\n', b'this', 'en')):
            with self.subTest(lang=lang, initial=initial):
                source = IMPORT + SHOW + lang + NOTE + initial + b' holds.\n'
                result = self.results(source, language=fallback)
                self.assertTrue(result[GAP].diagnostics)
                self.assertTrue(result[MARKER].diagnostics)
                self.assertFalse(result[GAP].edits or result[MARKER].edits)
                self.assertEqual(self.candidate(source, result), source)
        source = IMPORT + SHOW + UNMARKED
        self.assertEqual(self.candidate(source, self.results(source, language='en')),
                         explicit_break(source).replace(b'this consequence', b'#paragraph_tab\nThis consequence'))

    def test_nonliteral_structural_nested_altered_and_directive_boundaries_are_refused(self):
        for body in (
                b'First $x$ ends.#footnote[Reason.]\n\nThis holds.\n',
                b'#strong[First] ends.#footnote[Reason.]\n\nThis holds.\n',
                b'- First ends.#footnote[Reason.]\n\nThis holds.\n',
                b'= First ends.#footnote[Reason.]\n\nThis holds.\n',
                b'#block[First ends.#footnote[Reason.]\n\nThis holds.]\n',
                b'First ends.#footnote[Reason. #emph[More]]\n\nThis holds.\n',
                b'First ends.#footnote([Reason.])\n\nThis holds.\n',
                b'First ends.#footnote(numbering: "i")[Reason.]\n\nThis holds.\n',
                b'#let note = footnote\nFirst ends.#note[Reason.]\n\nThis holds.\n',
                b'#let footnote = unknown\nFirst ends.#footnote[Reason.]\n\nThis holds.\n',
                b'First ends.#footnote[Reason.] tail.\n\nThis holds.\n',
                b'First ends.#footnote[Reason.]#footnote[More.]\n\nThis holds.\n',
                b'First ends.#footnote[Reason.]\n\nThis *strong* holds.\n',
                b'First ends.#footnote[Reason.]\n\nThis $x$ holds.\n',
                b'First ends.#footnote[Reason.]\n\n#unknown[This holds.]\n',
                b'First ends.#footnote[Reason.]\n\n// attached comment\nThis holds.\n',
                b'First ends.#footnote[Reason.]\n\n'
                b'// typst-lint: ignore-next core.parenthesized-scripts -- preserve notation\nThis holds.\n',
                b'First // attached comment\nends.#footnote[Reason.]\n\nThis holds.\n',
                b'First ends.#footnote[Reason.] \n\nThis holds.\n',
                b'First ends.#footnote[Reason.]\nThis holds.\n',
                b'First ends.#footnote[Reason.]\r\rThis holds.\r',
                b'First ends.#footnote[Reason.]\n\n'):
            with self.subTest(body=body):
                source = PREFIX + body
                tree = parse(source)
                self.assertFalse(tree.nodes[0].errors)
                result = self.results(source)
                self.assertFalse(result[GAP].edits or result[MARKER].edits)
                self.assertEqual(self.candidate(source, result), source)

    def test_independent_repairs_keep_unrelated_existing_findings_and_refusals(self):
        source = PREFIX + b'$x^i$\n\n' + UNMARKED + b'\nSecond remains.\n'
        before, after, candidate, contexts = self.pair(source)
        expected = explicit_break(source).replace(b'$x^i$', b'$x^(i)$').replace(
            b'this consequence', b'#paragraph_tab\nThis consequence')
        self.assertEqual(candidate, expected)
        self.assertEqual(owners(before), {SCRIPT, MARKER, CAPITALIZATION})
        self.assertEqual({d['rule_id'] for d in after['diagnostics']}, {MARKER})
        self.assertTrue(any('initial-word-outside-editorial-allowlist' in reason
                            for row in after['coverage']['files'] if row['rule_id'] == MARKER
                            for reason in row['reasons']))
        self.assert_verified(source, before, after, candidate, contexts)

    def test_independent_audit_rejects_fabricated_gap_attribution_and_lost_evidence(self):
        source = PREFIX + UNMARKED
        before, after, candidate, contexts = self.pair(source)
        self.assert_verified(source, before, after, candidate, contexts)
        for kind in ('dual-owner', 'history-owner', 'missing-history', 'baseline-hash', 'new-refusal'):
            changed, changed_after = copy.deepcopy(before), copy.deepcopy(after)
            if kind == 'dual-owner':
                changed['proposed_edits'][0]['rule_ids'] = sorted({MARKER, GAP})
            elif kind == 'history-owner':
                first = changed['convergence_evidence'][0]['steps'][0]['applied'][0]
                first['rule_id'], first['rule_ids'] = GAP, [GAP]
            elif kind == 'missing-history':
                changed.pop('convergence_evidence')
            elif kind == 'baseline-hash':
                changed['proposed_edits'][0]['baseline_sha256'] = '0' * 64
            else:
                row = next(row for row in changed_after['coverage']['files'] if row['rule_id'] == MARKER)
                row['reasons'].append('New unsupported paragraph ownership at byte 0')
            with self.subTest(kind=kind):
                verdict = candidate_verdict(changed, changed_after, {'main.typ': source.decode()},
                                            {'main.typ': candidate.decode()}, analysis_contexts=contexts)
                self.assertFalse(verdict['verified'], verdict)
                self.assertEqual(verdict['status'], 'BLOCKED_ANALYSIS', verdict)
        verdict = candidate_verdict(before, after, {'main.typ': source.decode()},
                                    {'main.typ': candidate.decode()})
        self.assertFalse(verdict['verified'])

    def test_actual_cli_all_modes_remain_read_only_with_compiler_tripwire(self):
        output = self.root / 'previous.pdf'
        output.write_bytes(b'previous output must survive')
        compiler = self.root / 'bin' / 'typst'
        compiler.parent.mkdir()
        compiler.write_text('#!/bin/sh\nprintf called > "$COMPILER_MARKER"\nexit 91\n')
        compiler.chmod(0o700)
        marker = self.root / 'compiler-called'
        env = dict(os.environ, PATH=str(compiler.parent) + os.pathsep + os.environ.get('PATH', ''),
                   COMPILER_MARKER=str(marker), PYTHONDONTWRITEBYTECODE='1')
        config = self.config.read_bytes()
        for body, expected_owners in ((MARKED, {GAP}), (UNMARKED, {MARKER, CAPITALIZATION})):
            source = PREFIX + body
            self.target.write_bytes(source)
            expected = explicit_break(source)
            if body == UNMARKED:
                expected = expected.replace(b'this consequence', b'#paragraph_tab\nThis consequence')
            for mode in ('check', 'dry-run', 'fix', 'compile'):
                with self.subTest(body=body, mode=mode):
                    launcher = TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')
                    command = [sys.executable, str(launcher), 'fix' if mode == 'dry-run' else mode,
                               '--root', str(self.root), '--package-path', str(self.package_root), '--format', 'json']
                    if mode == 'dry-run':
                        command.append('--dry-run')
                    if mode == 'compile':
                        command.extend(['--', 'main.typ', 'previous.pdf'])
                    run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(run.returncode, 2, run.stderr)
                    value = json.loads(run.stdout)
                    self.assertEqual(proposed(source, value), expected)
                    self.assertEqual(owners(value), expected_owners)
                    self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                    self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                    self.assertTrue(all(e['baseline_sha256'] == digest(source) for e in value['proposed_edits']))
                    evidence, = value['package_evidence']
                    self.assertEqual(evidence['import_path'], TEXT_UTILS)
                    self.assertEqual(evidence['source_sha256'], TEXT_UTILS_SOURCE_SHA256)
                    self.assertEqual(self.target.read_bytes(), source)
                    self.assertEqual(self.config.read_bytes(), config)
                    self.assertEqual(output.read_bytes(), b'previous output must survive')
                    self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
