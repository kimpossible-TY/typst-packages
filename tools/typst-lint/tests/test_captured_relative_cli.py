"""Independent public graph CLI tests using owned parsers, stores and compilers.

No native compiler or font is used. Only the existing reviewed-artifact seam is
patched; the public dispatch, graph fix, publication and transaction are real.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_core as core
import captured_relative_compile as graph_compiler
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import closed_core_compile as runtime
import pdf_publication_store as output_store
import source_profiles
import transactions
import typst_lint as lint
from graph_compile_test_support import GraphFixture, pdf_bytes
from syntax_client import EXECUTABLE
from test_offline_compile_integration import _CompileFixtures, CLI, STYLE, OLD_OUTPUT

PROFILE = 'captured-relative-core-v1'
ORIGINAL = b'#import "values.typ": count\nPreserved prose #count $x_count$\r\n'
EXPECTED = ORIGINAL.replace(b'$x_count$', b'$x_(count)$')
MODULE = b'#let count = 7\n#let unused = "unchanged"\n'


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class _GraphCliFixtures(_CompileFixtures):
    def fixture(self, *, target='main.typ', sources=None, present=True, **kwargs):
        self.serial += 1
        if sources is None:
            module = str(Path(target).with_name('values.typ'))
            sources = {target: ORIGINAL, module: MODULE}
        f = GraphFixture(self.base / str(self.serial), target=target, sources=sources, **kwargs)
        f.source = f.root / f.target
        f.output = 'build with spaces/nested/document file.pdf'
        f.pdf = f.root / f.output
        f.pdf.parent.mkdir(parents=True)
        if present:
            f.pdf.write_bytes(OLD_OUTPUT)
            f.pdf.chmod(0o644)
        namespace = 'root-' + sha256(os.fsencode(f.root))
        f.source_store = f.base / '.typst-lint-journals'
        f.source_namespace = f.source_store / namespace
        f.output_store = f.base / '.typst-lint-pdf-journals'
        f.output_namespace = f.output_store / namespace
        f.compiler_marker = f.base / 'compile-started'
        f.ready, f.release = str(f.base / 'ready-'), str(f.base / 'release-')
        return f

    def argv(self, command='compile', f=None, *, format='json', output=None):
        f = f or self.f
        args = [command, '--root', str(f.root), '--format', format]
        if command in ('compile', 'fix'):
            args += ['--profile', PROFILE, '--offline-exclusive', '--target', f.target]
        if command == 'compile':
            args += ['--resource-profile', 'embedded-ascii-v1', '--offline-output-exclusive',
                     '--output', f.output if output is None else output]
        return args

    def request(self, f=None, argv=None, **options):
        f = f or self.f
        options.setdefault('source_profile', PROFILE)
        options.setdefault('catalog', str(f.catalog))
        options.setdefault('dependency_paths', list(f.sources))
        return super().request(f, argv, **options)

    def invoke(self, argv=None, *, f=None, gate=False):
        f = f or self.f
        stream = io.StringIO()
        with mock.patch.object(inputs, 'CATALOG', f.catalog), f.resources(), redirect_stdout(stream):
            status = lint.main(self.argv(f=f) if argv is None else argv, gate=gate)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload['status'], status)
        return payload

    def complete_state(self, path):
        info = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            attrs = transactions._xattrs(fd)
        finally:
            os.close(fd)
        return (self.file_state(path), info.st_ctime_ns, attrs)

    def events(self, f=None, *, source=False):
        return [[json.loads(p.read_bytes())['kind' if source else 'state'] for p in sorted(m.parent.glob('event-*.json'))]
                for m in self.journals(f, source=source)]

    def assert_membership(self, summary, f=None):
        f = f or self.f
        final = dict(f.sources)
        final[f.target] = f.source.read_bytes()
        self.assertEqual(summary['profile'], PROFILE)
        self.assertEqual(summary['root_grammar_version'], core.ROOT_GRAMMAR_VERSION)
        self.assertEqual(summary['module_grammar_version'], core.MODULE_GRAMMAR_VERSION)
        self.assertEqual(summary['parser_contract'], list(core.PARSER_CONTRACT))
        self.assertNotIn('grammar_version', summary)
        expected = [dict(path=p, sha256=sha256(raw), size=len(raw),
            role='writable_root' if p == f.target else 'unchanged_read_only_dependency')
            for p, raw in sorted(final.items())]
        self.assertEqual(summary['sources'], expected)
        self.assertEqual(summary['modified_paths'], [f.target] if summary['journal_id'] else [])
        self.assertFalse(summary['coverage']['global_complete'])
        self.assertEqual(summary['coverage']['global_partial_rule_count'], 33)
        self.assertEqual(len(summary['required_rule_ids']), 15)
        for edge in summary['import_edges']:
            self.assertEqual(set(edge), {'importer', 'request', 'target', 'node_id', 'span', 'items'})
            self.assertEqual(len(edge['span']), 2)
            self.assertIn(edge['importer'], f.sources)
            self.assertIn(edge['target'], f.sources)


class GraphCliArgumentTests(_GraphCliFixtures):
    def test_exact_selector_does_not_widen_legacy_grammar_helper(self):
        self.assertEqual(source_profiles.CAPTURED_RELATIVE_SOURCE_PROFILE, PROFILE)
        self.assertEqual(source_profiles.KNOWN_SOURCE_PROFILES,
                         {'closed-core-v1', 'structured-core-v1'})
        self.assertEqual(source_profiles.KNOWN_OFFLINE_SOURCE_PROFILES,
                         {'closed-core-v1', 'structured-core-v1', PROFILE,
                          'captured-text-utils-paragraph-v1'})
        with self.assertRaises(ValueError):
            source_profiles.grammar_for_source_profile(PROFILE)
        for value in (None, True, [], PROFILE.upper(), ' ' + PROFILE, PROFILE + ' ', 'captured-relative'):
            self.assertFalse(source_profiles.is_offline_source_profile(value))
        self.assertTrue(source_profiles.is_offline_source_profile(PROFILE))

    def test_invalid_selectors_duplicates_raw_args_and_targets_precede_all_access(self):
        good = self.argv()
        variants = []
        for option in ('--profile', '--resource-profile', '--offline-exclusive',
                       '--offline-output-exclusive', '--target', '--output'):
            args = list(good)
            index = args.index(option)
            del args[index:index + (1 if option.startswith('--offline-') else 2)]
            variants.append(args)
        for option in ('--root', '--profile', '--target', '--resource-profile', '--output', '--format'):
            index = good.index(option)
            variants.extend([good + good[index:index+2], good + [option + '=' + good[index+1]]])
        for tail in (['--offline-exclusive'], ['--offline-output-exclusive'], ['--dry-run'],
                     ['--', '--open'], ['--', '--profile', PROFILE], ['--compiler-path', '/bin/true'],
                     ['--deadline-seconds', '1'], ['--font-path', '/tmp'], ['--package-path', '/tmp'],
                     ['--output=x.pdf'], ['--out', 'x.pdf'], ['extra.typ'], ['--journal-id', 'a'*32]):
            variants.append(good + tail)
        for target in ('./main.typ', '../main.typ', '/tmp/main.typ', 'a//main.typ',
                       'a/../main.typ', 'main.TYP', 'main.typ/', 'a\\main.typ', 'x' * 509 + '.typ',
                       '한' * 170 + '.typ'):
            args = list(good)
            args[args.index('--target')+1] = target
            variants.append(args)
        for value in (PROFILE.upper(), ' '+PROFILE, PROFILE+' ', 'captured-relative'):
            args = list(good)
            args[args.index('--profile')+1] = value
            variants.append(args)
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('No filesystem access')), \
             mock.patch.object(transactions.OfflineExclusive,
                'assert_editors_watchers_closed_and_no_other_writers', side_effect=AssertionError('No source lease')), \
             mock.patch.object(output_store.OfflineOutputExclusive,
                'assert_exporters_watchers_closed_and_no_other_writers', side_effect=AssertionError('No output lease')):
            for args in variants:
                with self.subTest(args=args):
                    result = self.invoke(args)
                    self.assertEqual((result['status'], result['phase'], result['error_code']),
                                     (2, 'arguments', 'invalid_arguments'))

    def test_graph_options_refused_for_check_dry_run_gate_and_history(self):
        variants = [(self.argv('fix') + ['--dry-run'], False),
                    (['check'] + self.argv('fix')[1:], False), (self.argv(), True)]
        variants += [(self.argv(command) + ['--profile', PROFILE], False)
                     for command in ('journal-status', 'output-status')]
        variants += [(self.argv('fix') + ['--resource-profile', 'embedded-ascii-v1'], False)]
        before = self.tree_state(self.f.root)
        for args, gate in variants:
            with self.subTest(args=args, gate=gate):
                result = self.invoke(args, gate=gate)
                self.assertEqual(result['status'], 2)
                self.assert_no_compile(result)
        self.assertEqual(self.tree_state(self.f.root), before)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned compiler fixtures')
class GraphCliIntegrationTests(_GraphCliFixtures):
    def test_real_fix_exact_summary_and_idempotent_noop(self):
        module = self.complete_state(self.f.root / 'values.typ')
        unrelated = self.complete_state(self.f.unrelated)
        result = self.run_json(argv=self.argv('fix'))
        self.assertEqual(result['status'], 0, result)
        self.assert_no_compile(result)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_membership(result)
        self.assertEqual(result['original_sha256'], sha256(ORIGINAL))
        self.assertEqual(result['final_sha256'], sha256(EXPECTED))
        self.assertEqual(result['journal_id'], self.journals(source=True)[0].parent.name)
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        self.assertEqual(self.complete_state(self.f.root / 'values.typ'), module)
        self.assertEqual(self.complete_state(self.f.unrelated), unrelated)
        before = self.complete_state(self.f.source)
        again = self.run_json(argv=self.argv('fix'))
        self.assertEqual(again['status'], 0, again)
        self.assert_membership(again)
        self.assertIsNone(again['journal_id'])
        self.assertEqual(again['applied_insertions'], [])
        self.assertEqual(self.complete_state(self.f.source), before)
        self.assertEqual(len(self.journals(source=True)), 1)
        self.assertEqual(self.journals(), [])

    def test_transitive_diamond_unused_imports_and_metadata_are_unchanged(self):
        sources = {'chapters/main.typ': b'#import "../left.typ": left\n#import "../right.typ": right\n'
                   b'#import "../unused.typ": unused\n#left $right_1$\n',
                   'left.typ': b'#import "seed.typ": seed as incoming\n#let left = incoming\n',
                   'right.typ': b'#import "seed.typ": seed\n#let right = seed\n',
                   'seed.typ': b'#let seed = 7\n', 'unused.typ': b'#let unused = "untouched"\n'}
        f = self.fixture(target='chapters/main.typ', sources=sources)
        before = {p: self.complete_state(f.root / p) for p in sources if p != f.target}
        order = ['unused.typ', 'seed.typ', 'right.typ', f.target, 'left.typ']
        result = self.run_json(f, dependency_paths=order, assert_order=True)
        self.assertEqual(result['status'], 0, result)
        self.assert_membership(result['source_summary'], f)
        self.assertEqual(len(result['source_summary']['import_edges']), 5)
        self.assertEqual({p: self.complete_state(f.root / p) for p in before}, before)
        observation = result['graph_observation']
        self.assertTrue(result['graph_observation_available'])
        self.assertEqual(observation['dependency_paths'], order)
        self.assertEqual({s['path'] for s in observation['sources']}, set(sources))
        self.assertEqual(observation['aggregate_source_bytes'],
                         sum((f.root / p).stat().st_size for p in sources))
        self.assertEqual(observation['scalar_budget'], dict(policy=graph_compiler.SCALAR_RENDER_POLICY,
            limit=1048576, total_charge=2, use_count=2, origin_count=1))
        self.assertEqual(set(observation['dependencies']), {'path', 'identity', 'sha256', 'size', 'parents'})
        self.assertNotEqual(observation['dependency_paths'], [s['path'] for s in result['source_summary']['sources']])
        self.assertEqual(result['publication_report']['output_disposition'], 'published')
        self.assertEqual(result['compile_report']['safety_gate'], 'passed')
        self.assertEqual(result['compile_report']['resource_report']['fonts'], [])
        self.assertEqual([a['purpose'] for a in result['compile_report']['attempted_invocations']],
                         ['version-probe', 'compile'])
        self.assert_lock_free(f)

    def test_public_compile_success_then_noop_publishes_both_and_preserves_source(self):
        result = self.run_json(assert_order=True)
        self.assertEqual(result['status'], 0, result)
        self.assert_membership(result['source_summary'])
        self.assertEqual(result['source_commit'], 'committed')
        self.assertEqual(result['source_summary']['journal_id'], self.journals(source=True)[0].parent.name)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertIn(EXPECTED.hex().encode(), self.f.pdf.read_bytes())
        before = self.complete_state(self.f.source)
        self.f.compiler_marker.unlink()
        again = self.run_json(assert_order=True)
        self.assertEqual(again['status'], 0, again)
        self.assertIsNone(again['source_summary']['journal_id'])
        self.assertEqual(again['source_summary']['modified_paths'], [])
        self.assertEqual(self.complete_state(self.f.source), before)
        self.assertEqual(len(self.journals(source=True)), 1)
        self.assertEqual(len(self.journals()), 2)
        self.assertEqual([events[-1] for events in self.events()], ['published', 'published'])

    def test_public_compile_uses_independent_leases_and_fixed_ten_second_deadline(self):
        actual = graph_compiler.compile_committed_graph
        deadlines = []
        def compile_graph(*args, **kwargs):
            deadlines.append(kwargs['deadline_seconds'])
            return actual(*args, **kwargs)
        with mock.patch.object(graph_compiler, 'compile_committed_graph', compile_graph), \
             mock.patch.object(transactions.OfflineExclusive,
                'assert_editors_watchers_closed_and_no_other_writers',
                wraps=transactions.OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers) as source, \
             mock.patch.object(output_store.OfflineOutputExclusive,
                'assert_exporters_watchers_closed_and_no_other_writers',
                wraps=output_store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers) as output:
            result = self.invoke()
        self.assertEqual(result['status'], 0, result)
        self.assertEqual(deadlines, [10.0])
        self.assertEqual(source.call_count, 1)
        self.assertEqual(output.call_count, 1)

    def test_report_alias_mutation_during_compile_cannot_forge_applied_summary(self):
        actual = graph_compiler.compile_committed_graph
        def compile_graph(fix, *args, **kwargs):
            summary = fix.summary
            object.__setattr__(summary.sources[0], 'sha256', '0' * 64)
            object.__setattr__(summary, 'modified_paths', ('forged.typ',))
            return actual(fix, *args, **kwargs)
        with mock.patch.object(graph_compiler, 'compile_committed_graph', compile_graph):
            result = self.invoke()
        self.assertEqual(result['status'], 0, result)
        self.assert_membership(result['source_summary'])
        self.assertTrue(result['graph_observation_available'])
        self.assertEqual(result['profile'], PROFILE)
        self.assertEqual(result['root_grammar_version'], core.ROOT_GRAMMAR_VERSION)
        self.assertEqual(result['module_grammar_version'], core.MODULE_GRAMMAR_VERSION)
        self.assertEqual(result['parser_contract'], list(core.PARSER_CONTRACT))
        self.assertEqual(result['graph_observation']['sources'][0]['sha256'], sha256(EXPECTED))
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_report_alias_mutation_at_context_exit_cannot_forge_summary(self):
        actual_exit = bridge.ProtectedCapturedRelativeFix.__exit__
        for command, fail_exit in (('compile', False), ('fix', True)):
            with self.subTest(command=command, fail_exit=fail_exit):
                f = self.fixture()
                def exit_fix(fix, *args):
                    summary = fix.summary
                    result = actual_exit(fix, *args)
                    object.__setattr__(summary.sources[0], 'sha256', '0' * 64)
                    object.__setattr__(summary, 'modified_paths', ('forged.typ',))
                    if fail_exit:
                        raise OSError('Owned later source context close failure')
                    return result
                with mock.patch.object(bridge.ProtectedCapturedRelativeFix, '__exit__', exit_fix):
                    result = self.invoke(self.argv(command, f), f=f)
                self.assertEqual(result['status'], 3 if fail_exit else 0, result)
                self.assert_membership(result['source_summary'] if command == 'compile' else result, f)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(self.events(f, source=True)[0][-1], 'committed')
                if command == 'compile':
                    self.assertEqual(result['profile'], PROFILE)
                    self.assertEqual(result['root_grammar_version'], core.ROOT_GRAMMAR_VERSION)
                    self.assertEqual(result['module_grammar_version'], core.MODULE_GRAMMAR_VERSION)
                    self.assertEqual(result['parser_contract'], list(core.PARSER_CONTRACT))
                    self.assertEqual(result['graph_observation']['sources'][0]['sha256'], sha256(EXPECTED))
                    self.assertEqual(result['publication_report']['output_disposition'], 'published')

    def test_postcommit_apply_guard_refusal_retains_genuine_summary_and_journal(self):
        actual = bridge.ProtectedCapturedRelativeFix._assert_validation
        def guard(fix, validation):
            if fix._summary_facts is not None:
                raise bridge.CapturedRelativeFixError('Owned postcommit apply guard refusal')
            return actual(fix, validation)
        with mock.patch.object(bridge.ProtectedCapturedRelativeFix, '_assert_validation', guard):
            result = self.invoke()
        self.assertEqual(result['status'], 3, result)
        self.assertEqual(result['source_commit'], 'committed')
        self.assert_membership(result['source_summary'])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        self.assertFalse(result['compiler_invoked'])
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)

    def test_same_imported_document_still_refused_by_both_old_profiles(self):
        for profile in ('closed-core-v1', 'structured-core-v1'):
            for command in ('fix', 'compile'):
                with self.subTest(profile=profile, command=command):
                    f = self.fixture()
                    args = self.argv(command, f)
                    args[args.index(PROFILE)] = profile
                    before = self.tree_state(f.root)
                    result = self.run_json(f, argv=args)
                    self.assertEqual(result['status'], 2, result)
                    self.assert_no_compile(result, f)
                    self.assertEqual(self.tree_state(f.root), before)
                    self.assertEqual(self.journals(f, source=True), [])

    def test_bare_compile_and_style_remain_inert(self):
        for executable in (CLI, STYLE):
            for command in ('compile', 'check', 'fix'):
                args = [command, '--root', str(self.f.root), '--format', 'json']
                before = self.tree_state(self.f.root)
                result = self.run_process(argv=args, actual=True, gate=executable == STYLE)
                self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                self.assertEqual(self.tree_state(self.f.root), before)
                self.assertFalse(self.f.compiler_marker.exists())

    def test_unsupported_root_module_missing_import_and_encoding_are_input_errors(self):
        invalid = [('main.typ', b'#read("secret")\n'), ('values.typ', b'#let count = read("secret")\n'),
                   ('main.typ', b'#import "missing.typ": count\n#count\n'),
                   ('main.typ', b'\xff\n'), ('values.typ', b'#let count = "\xff"\n')]
        for name, data in invalid:
            for command in ('fix', 'compile'):
                with self.subTest(name=name, data=data, command=command):
                    f = self.fixture()
                    f.put(name, data)
                    before = self.tree_state(f.root)
                    result = self.run_json(f, argv=self.argv(command, f))
                    self.assertEqual(result['status'], 2, result)
                    self.assert_no_compile(result, f)
                    self.assertEqual(self.tree_state(f.root), before)
                    self.assertEqual(self.journals(f, source=True), [])
                    self.assertEqual(self.journals(f), [])

    def test_graph_budget_refusals_precede_apply_and_resources(self):
        for constant, limit in (('MAX_FILES', 1), ('MAX_SOURCE_BYTES', 1),
                                ('MAX_TOTAL_NODES', 1), ('MAX_IMPORT_DEPTH', 0)):
            with self.subTest(constant=constant):
                f = self.fixture()
                before = self.tree_state(f.root)
                with mock.patch.object(core, constant, limit), \
                     mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('No resource capture')):
                    result = self.invoke(f=f)
                self.assertEqual(result['status'], 3, result)
                self.assertEqual(result['error_code'], 'unsafe_transaction')
                self.assertEqual(self.tree_state(f.root), before)
                self.assertEqual(self.journals(f, source=True), [])

    def test_scalar_budget_and_dependency_only_repertoire_refuse_before_resource_capture(self):
        for case in ('scalar', 'repertoire'):
            with self.subTest(case=case):
                f = self.fixture()
                if case == 'repertoire':
                    f.put('values.typ', '#let count = 7\n#let unused = "한글"\n'.encode())
                budget = (mock.patch.object(graph_compiler, 'MAX_SCALAR_RENDER_BYTES', 0)
                    if case == 'scalar' else mock.patch.object(runtime, '_capture_fonts',
                        side_effect=AssertionError('No font capture')))
                with budget, mock.patch.object(runtime, '_capture_compiler',
                        side_effect=AssertionError('No compiler capture')):
                    result = self.invoke(f=f)
                self.assertEqual(result['status'], 3 if case == 'scalar' else 2, result)
                self.assertEqual(result['source_commit'], 'committed')
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertFalse(result['compiler_invoked'])
                self.assertEqual(result['publication_report']['attempted_invocations'], [])
                self.assertIsNone(result['publication_report']['resource_report'])
                if 'compile_report' in result:
                    self.assertEqual(result['compile_report']['attempted_invocations'], [])
                    self.assertIsNone(result['compile_report']['resource_report'])
                self.assertFalse(result['graph_observation_available'])
                self.assertIsNone(result['graph_observation'])
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)

    def test_stale_dependency_between_capture_and_validation_preserves_later_edit(self):
        original = bridge.ProtectedCapturedRelativeFix.validate
        module = self.f.root / 'values.typ'
        def validate(fix, *args, **kwargs):
            module.write_bytes(b'#let count = 9\n')
            return original(fix, *args, **kwargs)
        with mock.patch.object(bridge.ProtectedCapturedRelativeFix, 'validate', validate):
            result = self.invoke()
        self.assertEqual(result['status'], 3, result)
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assertEqual(module.read_bytes(), b'#let count = 9\n')
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.journals(source=True), [])
        self.assertFalse(result['compiler_invoked'])

    def test_output_preparation_and_source_collisions_precede_validate_apply(self):
        for boundary in (['output', 'after_prepared', self.f.output],
                         ['output', 'before_event', 'prepared']):
            f = self.fixture()
            result = self.run_json(f, fail_boundary=boundary, forbid_validate=True)
            self.assertEqual(result['status'], 3, result)
            self.assert_no_compile(result, f)
            self.assertEqual(f.source.read_bytes(), ORIGINAL)
            self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
            self.assertEqual(self.journals(f, source=True), [])
        for output in ('main.typ', 'values.typ', '.typst-lint.toml'):
            # Extension rejection is part of literal output parsing, before root access.
            result = self.run_json(argv=self.argv(output=output), forbid_leases=True)
            self.assertEqual(result['status'], 2, result)
        f = self.fixture()
        alias = f.root / 'collision.pdf'
        os.link(f.root / 'values.typ', alias)
        before = self.tree_state(f.root)
        result = self.run_json(f, argv=self.argv(f=f, output='collision.pdf'), forbid_validate=True)
        self.assertEqual(result['status'], 3, result)
        self.assertEqual(self.tree_state(f.root), before)
        self.assertEqual(self.journals(f, source=True), [])

    def test_postcommit_guard_failure_retains_real_applied_summary(self):
        result = self.run_json(mutate_boundary=['output', 'before_compile', self.f.output], mutate_kind='config')
        self.assertEqual(result['status'], 3, result)
        self.assertEqual(result['source_commit'], 'committed')
        self.assert_membership(result['source_summary'])
        self.assertEqual(result['source_summary']['journal_id'], self.journals(source=True)[0].parent.name)
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        self.assert_no_compile(result)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)

    def test_failed_apply_retains_original_transaction_failure_and_recovery_report(self):
        result = self.run_json(fail_boundary=['source', 'after_replace', self.f.target])
        self.assertEqual((result['status'], result['error_code'], result['phase']),
                         (3, 'transaction_failure', 'apply'), result)
        self.assertIn('Owned CLI publication/transaction failure', result['error'])
        self.assertIn('recovery_report', result)
        self.assertFalse(result['recovery_report']['complete'])
        self.assertEqual(result['recovery_report'], result['source_recovery']['recovery_report'])
        self.assertEqual(result['recovery_report']['journal'], self.journals(source=True)[0].parent.name)
        self.assertEqual(result['recovery_report']['restored'], [self.f.target])
        self.assertEqual(result['source_recovery']['state'], 'required')
        self.assertEqual(result['source_commit'], 'unknown')
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assert_no_compile(result)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertNotIn('committed', self.events(source=True)[0])

    def test_raw_compiler_signal_with_later_context_failure_becomes_safety_three(self):
        result = self.run_json(compiler_mode=f'signal-{int(signal.SIGTERM)}', source_exit_failure=True)
        self.assertEqual(result['status'], 3, result)
        self.assertNotIn('termination_signal', result)
        self.assertTrue(result['compiler_invoked'])
        self.assertEqual(result['compiler_status'], -signal.SIGTERM)
        self.assertEqual(result['compile_report']['compiler_failure']['kind'], 'process_exit')
        self.assertEqual(result['compile_report']['attempted_invocations'][-1]['returncode'], -signal.SIGTERM)
        self.assert_membership(result['source_summary'])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_lock_free()

    def test_raw_nonzero_signal_timeout_and_probe_failures_keep_honest_attempts(self):
        cases = [('nonzero', 19, 'process_exit', True),
                 (f'signal-{int(signal.SIGTERM)}', -int(signal.SIGTERM), 'process_exit', True),
                 ('timeout', 3, 'timeout', True), ('probe-exit', 3, 'process_exit', False),
                 ('launch-failure', 3, None, False)]
        for mode, status, kind, invoked in cases:
            with self.subTest(mode=mode):
                f = self.fixture()
                result = self.run_json(f, compiler_mode=mode)
                self.assertEqual(result['status'], status, result)
                if kind is None:
                    self.assertIsNone(result['compile_report']['compiler_failure'])
                    self.assertEqual(result['compile_report']['attempted_invocations'][-1]['lifecycle'], 'launch_failed')
                    self.assertFalse(result['compile_report']['attempted_invocations'][-1]['started'])
                else:
                    self.assertEqual(result['compile_report']['compiler_failure']['kind'], kind)
                self.assertIs(result['compiler_invoked'], invoked)
                self.assertFalse(result['graph_observation_available'])
                self.assertIsNone(result['graph_observation'])
                self.assertEqual(result['source_summary']['final_sha256'], sha256(EXPECTED))
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                if mode == 'nonzero':
                    attempt = result['compile_report']['attempted_invocations'][-1]
                    self.assertEqual(attempt['returncode'], 19)
                    self.assertEqual(attempt['stdout_hex'], b'owned stdout\x00\xff'.hex())
                    self.assertEqual(attempt['stderr_hex'], b'owned stderr\xfe\n'.hex())
                self.assert_lock_free(f)

    def test_malformed_actual_dependencies_do_not_invent_observations(self):
        for paths in (['main.typ'], ['main.typ', 'values.typ', 'values.typ'],
                      ['main.typ', '../values.typ'], ['values.typ', 'main.typ', 'unselected.typ']):
            with self.subTest(paths=paths):
                f = self.fixture()
                result = self.run_json(f, dependency_paths=paths)
                self.assertEqual(result['status'], 3, result)
                self.assertIs(result['compiler_invoked'], True)
                self.assertEqual(result['compiler_status'], 0)
                self.assertEqual(result['compile_report']['safety_gate'], 'refused')
                self.assertIsNone(result['compile_report']['compiler_failure'])
                self.assertFalse(result['graph_observation_available'])
                self.assertIsNone(result['graph_observation'])
                self.assertIn('unavailable', result['graph_observation_note'].lower())
                self.assert_membership(result['source_summary'], f)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)

    def test_invalid_dependency_encoding_terminator_and_empty_record_keep_zero_status_facts(self):
        for raw in (b'main.typ\0values.typ', b'main.typ\0\xff\0',
                    b'main.typ\0values.typ\0\0'):
            with self.subTest(raw=raw):
                f = self.fixture()
                f.program(f'output.write_bytes({pdf_bytes()!r}); '
                          f'deps.write_bytes({raw!r}); raise SystemExit(0)')
                result = self.invoke(f=f)
                self.assertEqual(result['status'], 3, result)
                self.assertTrue(result['compiler_invoked'])
                self.assertEqual(result['compiler_status'], 0)
                self.assertEqual(result['compile_report']['safety_gate'], 'refused')
                self.assertIsNone(result['compile_report']['compiler_failure'])
                self.assertFalse(result['graph_observation_available'])
                self.assertIsNone(result['graph_observation'])
                self.assert_membership(result['source_summary'], f)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_lock_free(f)

    def test_later_zero_status_safety_failure_retains_real_observations(self):
        result = self.run_json(mutate_boundary=['output', 'after_compiled', self.f.output], mutate_kind='config')
        self.assertEqual(result['status'], 3, result)
        self.assertEqual(result['compiler_status'], 0)
        self.assertTrue(result['graph_observation_available'])
        self.assertEqual(result['graph_observation']['dependency_paths'], list(self.f.sources))
        self.assertEqual(result['compile_report']['safety_gate'], 'passed')
        self.assertEqual(result['source_commit'], 'committed')
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)


if __name__ == '__main__':
    unittest.main()
