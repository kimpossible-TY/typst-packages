"""Trusted closed-core repair bridge for the explicit offline CLI profile.

Only one explicitly configured literal .typ input is admitted. This does not
certify general Typst projects, the PDE pack, dependency graphs or compilation.
The runtime/backend code is trusted; serialized reports cannot confer authority.
The caller must hold the real offline-exclusive assertion for the whole context.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import tomllib
from types import MappingProxyType

from candidate_convergence import converge
from closed_core import (CORE_RULE_IDS as ADMISSION_CORE_RULE_IDS,
                         certify, verify_proof)
from edit_plan import plan
from script_rule import RULE_ID as SCRIPT, ScriptResult, analyze as analyze_script
from syntax_client import parse
from transactions import (DEFAULT_READ_LIMITS, EvidenceInput, GraphAnalysis,
                          GraphSnapshot, ProtectedTransaction, SourceEdit,
                          TransactionError)
from typst_lint import CATALOG, InputError, digest, parse_rules, relative

REVIEWED_CATALOG_SHA256 = '955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826'
# Compiled authority, never a subset selected from a mutable catalog/config.
_CORE_POLICIES = MappingProxyType({
    'core.parenthesized-scripts': ('warning', 'automatic'),
    'core.scoped-function-call': ('error', 'automatic'),
    'core.scoped-marker-tags': ('warning', 'diagnostic-only'),
    'core.scoped-anchor-access': ('warning', 'conditional'),
    'core.scoped-reference-access': ('warning', 'conditional'),
    'core.local-overlay-api': ('warning', 'diagnostic-only'),
    'core.annotation-in-display': ('error', 'diagnostic-only'),
    'core.annotation-marker-bounds': ('error', 'diagnostic-only'),
    'core.unique-scope-namespace': ('error', 'diagnostic-only'),
    'core.project-ref-arguments': ('error', 'conditional'),
    'core.project-ref-catalog-target': ('error', 'diagnostic-only'),
    'core.single-book-show': ('error', 'diagnostic-only'),
    'core.no-duplicate-book-reset': ('error', 'conditional'),
    'core.no-set-theme': ('error', 'diagnostic-only'),
    'core.theme-in-context': ('warning', 'conditional'),
})
CORE_RULE_IDS = frozenset(_CORE_POLICIES)
_PDE_RULE_IDS = frozenset({
    'pde.import-order', 'pde.no-redundant-facade-import',
    'pde.paragraph-marker-gap', 'pde.paragraph-marker-target',
    'pde.prose-paragraph-marker', 'pde.continuation-line',
    'pde.display-continuation-gap', 'pde.proof-trailing-gap',
    'pde.footnote-trailing-gap', 'pde.prose-footnote-punctuation',
    'pde.aligned-footnote-spacer', 'pde.unaligned-footnote-no-spacer',
    'pde.prose-footnote-no-spacer', 'pde.standalone-display',
    'pde.text-only-highlight', 'pde.theme-cancelmark',
    'pde.flowbox-colored-border', 'pde.paragraph-capitalization',
})
_CONFIG = '.typst-lint.toml'
_RULE_FIELDS = frozenset({'id', 'grade', 'source', 'description', 'repair',
                          'rewrite', 'preconditions'})


class ProtectedCoreError(TransactionError):
    """The deliberately narrow protected profile was not fully proven."""


class ProtectedCoreInputError(ProtectedCoreError, InputError):
    """Invalid explicit target/config/catalog, still caught by the old bridge API."""
    def __init__(self, message, *, error_code='invalid_configuration', phase='configuration'):
        self.error_code = error_code
        self.phase = phase
        super().__init__(message)


def _input_require(condition, message, **category):
    if not condition:
        raise ProtectedCoreInputError(message, **category)


def _require(condition, message):
    if not condition:
        raise ProtectedCoreError(message)


def _literal_target(target):
    try:
        relative(target)
    except InputError as error:
        raise ProtectedCoreInputError(str(error), error_code='invalid_target', phase='arguments') from error
    _input_require(target.endswith('.typ') and not any(c in target for c in '*?[]{}'),
                   'Protected core requires one literal canonical .typ target',
                   error_code='invalid_target', phase='arguments')
    return target


def _config(raw, target):
    _input_require(type(raw) is bytes, 'Explicit present configuration bytes required')
    try:
        data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ProtectedCoreInputError(f'Invalid protected configuration: {error}') from error
    _input_require({'rule-set', 'include'} <= data.keys()
             and not (data.keys() - {'rule-set', 'include', 'entrypoint'}),
             'Protected core configuration requires explicit rule-set/include and no unsupported keys')
    _input_require(data['rule-set'] == 'core', 'Protected core requires the core pack')
    _input_require(type(data['include']) is list and data['include'] == [target],
             'Protected core include must select exactly the literal target')
    _input_require('entrypoint' not in data or data['entrypoint'] == target,
             'Protected core entrypoint must be absent or the same literal target')
    return digest(raw)


def _catalog_require(condition, message):
    _input_require(condition, message, error_code='invalid_catalog', phase='catalog')


def _catalog(raw):
    _catalog_require(type(raw) is bytes, 'Complete present catalog evidence required')
    _catalog_require(digest(raw) == REVIEWED_CATALOG_SHA256,
             'Protected core requires the exact reviewed full catalog digest')
    try:
        data, sha256 = parse_rules(raw)
    except (InputError, ValueError, UnicodeDecodeError) as error:
        raise ProtectedCoreInputError(f'Invalid protected rule catalog: {error}', error_code='invalid_catalog', phase='catalog') from error
    _catalog_require(set(data) == {'schema', 'version', 'packs', 'rules'},
             'Unknown protected catalog fields')
    records = data['rules']
    _catalog_require({r['id'] for r in records} == CORE_RULE_IDS | _PDE_RULE_IDS,
             'Protected catalog IDs differ from the compiled reviewed set')
    _catalog_require(frozenset(ADMISSION_CORE_RULE_IDS) == CORE_RULE_IDS,
             'Admission and protected core rule sets disagree')
    for record in records:
        _catalog_require(set(record) == _RULE_FIELDS, 'Unknown protected rule metadata')
        if record['id'] in CORE_RULE_IDS:
            _catalog_require((record['grade'], record['repair']) == _CORE_POLICIES[record['id']],
                     'Protected core rule repair metadata changed')
    # No mutable list/dict crosses into the actual script backend.
    return tuple(MappingProxyType(dict(record)) for record in records), sha256


def _attachment_roles(tree):
    return tuple(tuple(role for role, _ in node.operands)
                 for node in tree.nodes if node.kind == 'MathAttach')


def _operand_records(tree, path):
    """Semantic IDs survive inserted grouping; no byte offsets enter identity.

    This profile only inserts script parentheses and requires a clean final
    state. It does not claim stable IDs across arbitrary Typst rewrites.
    """
    records = []
    ordinal = 0
    for node in tree.nodes:
        if node.kind != 'MathAttach':
            continue
        ordinal += 1
        for role, operand_id in node.operands:
            operand = tree.nodes[operand_id]
            children = tuple(tree.nodes[i] for i in operand.children)
            parenthesized = (operand.kind == 'Math' and children
                             and children[0].kind == 'LeftParen'
                             and children[-1].kind == 'RightParen')
            line = tree.source[:operand.start].count(b'\n') + 1
            start = tree.source.rfind(b'\n', 0, operand.start) + 1
            column = len(tree.source[start:operand.start].decode('utf-8')) + 1
            identity = f'{path}:{SCRIPT}:attachment-{ordinal}:{role}'
            records.append((identity, line, column, bool(parenthesized)))
    return tuple(records)


def _findings(tree, path, result):
    expected = tuple(r for r in _operand_records(tree, path) if not r[3])
    observed = tuple((d.path, d.rule_id, d.line, d.column) for d in result.diagnostics)
    _require(observed == tuple((path, SCRIPT, line, col) for _, line, col, _ in expected),
             'Script diagnostics do not account for every admitted operand')
    return frozenset(identity for identity, _, _, _ in expected)


def _script(tree, path, catalog):
    proof = certify(tree, path)
    verify_proof(proof, tree, path)
    _require(tuple(proof.evaluated_rule_ids) == (SCRIPT,)
             and frozenset(proof.not_applicable_rule_ids) == CORE_RULE_IDS - {SCRIPT}
             and frozenset(rid for rid, reason in proof.structural_reasons if reason)
             == CORE_RULE_IDS - {SCRIPT}, 'Incomplete per-input core admission proof')
    result = analyze_script(tree, path, _verify=False, catalog=catalog)
    _require(type(result) is ScriptResult
             and all(type(getattr(result, field)) is tuple for field in
                     ('diagnostics', 'edits', 'skips', 'parse_errors', 'suppressed')),
             'Immutable actual script analysis required')
    _require(not (result.skips or result.parse_errors or result.suppressed),
             'Skipped, suppressed or parse-error script analysis is unsupported')
    findings = _findings(tree, path, result)
    _require(bool(findings) == bool(result.edits),
             'Every script finding must have an actual repair in this profile')
    return result, findings


@dataclass(frozen=True)
class _Pass:
    tree: object
    result: ScriptResult
    analysis: GraphAnalysis


@dataclass(frozen=True, slots=True)
class AppliedInsertion:
    path: str
    start: int
    end: int
    replacement: bytes
    rule_id: str = SCRIPT


@dataclass(frozen=True, slots=True)
class ProtectedCoreSummary:
    """Reporting only. Neither this value nor its serialization authorizes apply."""
    profile: str
    target: str
    original_sha256: str
    final_sha256: str
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    applied_insertions: tuple[AppliedInsertion, ...]
    modified_paths: tuple[str, ...] = ()
    journal_id: str | None = None


class ProtectedCoreFix:
    """One internal closed-core transaction with independent provenance replay.

    `fault` is solely a transaction test hook. This class never asserts an
    OfflineExclusive lease, invokes a compiler, installs
    a dependency, or widens the fixed built-in catalog/profile. Explicit callers
    may request transaction-owned creation of the fixed CLI journal store.
    """
    def __init__(self, root: Path, target: str, *, journal_directory: Path,
                 lease, limits=DEFAULT_READ_LIMITS, fault=None,
                 create_journal_directory=False):
        self.target = _literal_target(target)
        self._tx = ProtectedTransaction(root, (self.target,),
            journal_directory=journal_directory, lease=lease, limits=limits,
            evidence=(EvidenceInput(Path(root) / _CONFIG, allow_missing=True), EvidenceInput(CATALOG)),
            fault=fault, create_journal_directory=create_journal_directory)
        self._config_path = str(self._tx.root / _CONFIG)
        self._catalog_path = str(Path(CATALOG).absolute())
        self._evidence_paths = frozenset((self._config_path, self._catalog_path))
        self._baseline = None
        self._active = False
        self._candidate = None
        self._history = []
        self._validated_summary = None
        self._summary = None

    @property
    def transaction(self):
        """Internal inspection/testing surface, not another bridge token issuer."""
        return self._tx

    @property
    def summary(self):
        """Immutable independently verified reporting, available only after apply."""
        _require(self._summary is not None, 'A successful protected apply is required for reporting')
        return self._summary

    def __enter__(self):
        self._tx.__enter__()
        try:
            snapshot = self._tx.snapshot
            self._context(snapshot)
            self._baseline = snapshot.sources[self.target]
            self._active = True
            return self
        except BaseException:
            self._tx.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        self._candidate = None
        return self._tx.__exit__(exc_type, exc, tb)

    def _context(self, graph):
        _require(type(graph) is GraphSnapshot and graph.root == self._tx.root,
                 'Protected graph root differs from the captured transaction')
        _require(frozenset(graph.sources) == frozenset((self.target,))
                 and type(graph.sources[self.target]) is bytes,
                 'Protected graph requires exactly one captured source')
        _require(frozenset(graph.evidence) == self._evidence_paths,
                 'Additional or missing protected graph evidence is unsupported')
        # Both retained evidence values are actually parsed, never reread here.
        _config(graph.evidence[self._config_path], self.target)
        catalog, _ = _catalog(graph.evidence[self._catalog_path])
        return catalog

    def _analyze_graph(self, graph):
        _require(self._active, 'Active protected core context required')
        catalog = self._context(graph)
        tree = parse(graph.sources[self.target])
        result, findings = _script(tree, self.target, catalog)
        _require(not result.edits or tree.source == self._baseline,
                 'Protected core v1 refuses a second repair pass')
        analysis = GraphAnalysis(CORE_RULE_IDS, CORE_RULE_IDS,
            frozenset((self.target,)), self._evidence_paths,
            findings=findings,
            edits=tuple(SourceEdit(self.target, edit) for edit in result.edits))
        self._history.append(_Pass(tree, result, analysis))
        return analysis

    def _independent_replay(self, snapshot):
        catalog = self._context(snapshot)
        tree = parse(self._baseline)
        initial, _ = _script(tree, self.target, catalog)

        def analyze(checked):
            return {SCRIPT: _script(checked, self.target, catalog)[0]}

        def step(before, old, after, new, history):
            _require(not history.deferred and not history.moves,
                     'Protected script pass has conflicts or source movement')
            _require(_attachment_roles(before) == _attachment_roles(after),
                     'Script repair changed semantic attachment identities')
            _require(history.edits == plan(before.source, old[SCRIPT].edits).edits
                     and history.after == plan(before.source, old[SCRIPT].edits).candidate(),
                     'Independent script replay differs from exact raw proposals')
            _require(all(e.start == e.end and e.replacement
                         and not e.replacement.strip(b'()') for e in history.edits),
                     'Protected script repair must preserve every original source byte')
            _require(not (new[SCRIPT].diagnostics or new[SCRIPT].edits),
                     'Protected core v1 requires zero findings after one pass')

        def final(candidate):
            _require(candidate.passes <= 1 and not candidate.final_results[SCRIPT].diagnostics
                     and not candidate.final_results[SCRIPT].edits,
                     'Protected core candidate is not a clean one-pass result')
            _require(all(item.primary_rule_id == SCRIPT and item.rule_ids == (SCRIPT,)
                         and item.contributions
                         and all(c.rule_id == SCRIPT and c.pass_number == 1
                                 for c in item.contributions) for item in candidate.edits),
                     'Independent candidate has unowned repair attribution')
            _require(plan(candidate.baseline, (e.edit for e in candidate.edits)).candidate()
                     == candidate.content, 'Independent original-source replay mismatch')

        candidate = converge(tree, {SCRIPT: initial}, analyze, step, final, max_passes=1)
        final(candidate)
        return candidate

    def validate(self):
        _require(self._active, 'Active protected core context required')
        self._candidate = None
        self._history = []
        self._validated_summary = None
        self._summary = None
        snapshot = self._tx.snapshot
        token = self._tx.validate(self._analyze_graph, required_rules=CORE_RULE_IDS)
        _require(not token.findings and token.passes <= 1,
                 'Protected core v1 requires a clean one-pass transaction candidate')
        replay = self._independent_replay(snapshot)
        _require(token.sources == {self.target: replay.content}
                 and token.passes == replay.passes,
                 'Transaction and independent provenance candidate disagree')
        expected_sources = ((self._baseline, replay.content, replay.content)
                            if token.passes else (self._baseline, self._baseline))
        _require(tuple(p.tree.source for p in self._history) == expected_sources,
                 'Transaction analysis pass history differs from independent replay')
        _require(self._history[0].result == replay.initial_results[SCRIPT]
                 and all(p.result == replay.final_results[SCRIPT]
                         and not p.analysis.findings for p in self._history[1:]),
                 'Transaction attribution/results differ from independent analysis')
        if replay.steps:
            _require(tuple(e.edit for e in self._history[0].analysis.edits)
                     == replay.steps[0].edits,
                     'Transaction edit attribution differs from independent pass')
        # Independent replay is outside the analysis callback; recheck the live
        # captured identities/bytes again before blessing this bridge token.
        self._tx._revalidate()
        proof = certify(replay.final_tree, self.target)
        verify_proof(proof, replay.final_tree, self.target)
        self._validated_summary = ProtectedCoreSummary(
            'closed-core-v1', self.target, digest(self._baseline), digest(replay.content),
            tuple(sorted(CORE_RULE_IDS)), tuple(proof.evaluated_rule_ids),
            tuple(proof.structural_reasons),
            tuple(AppliedInsertion(self.target, item.edit.start, item.edit.end,
                                   item.edit.replacement) for item in replay.edits))
        self._candidate = token
        return token

    def apply(self, candidate):
        _require(self._active and candidate is not None and candidate is self._candidate,
                 'Only this bridge session\'s independently validated candidate may be applied')
        self._candidate = None
        result = self._tx.apply(candidate)
        # This reporting-only value is never an input to token validation/apply.
        self._summary = replace(self._validated_summary, modified_paths=result.modified,
                                journal_id=result.journal)
        return result
