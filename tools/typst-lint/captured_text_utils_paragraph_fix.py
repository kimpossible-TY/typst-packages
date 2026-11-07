"""Internal root-only paragraph capitalization over transaction-retained inputs.

This bridge owns one capture session, permits one ASCII case repair pass and
supplies a distinct live committed receipt after successful apply. It never
writes package evidence or admits older compiler/profile authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import weakref

import captured_text_utils_paragraph_core as core
from captured_text_utils_paragraph_inputs import CapturedTextUtilsParagraphInputSession
from candidate_convergence import (AttributedEdit, Candidate, Contribution, Step,
                                   converge)
from capitalization import (BodyKind, Context, MarkerBinding, StartKind,
                            evaluate as evaluate_capitalization)
from edit_plan import Edit, plan
from facade_provenance import InitializerCertificate
from paragraph_rules import (CAPITALIZATION, MARKER_GAP, MARKER_TARGET,
                             ParagraphResult, analyze as analyze_paragraphs)
from protected_fix import (_matches_authority as _matches_base,
                           _retain_authority as _retain_base)
from script_rule import RULE_ID as SCRIPT, ScriptResult, analyze as analyze_script
from static_resolver import PackageSource, TEXT_UTILS, resolve
from syntax_client import AdapterError, Tree, parse
from transactions import (DEFAULT_READ_LIMITS, CommittedSnapshot, GraphAnalysis, GraphSnapshot,
                          SourceEdit, TransactionError)
from typst_lint import InputError, SafetyError, digest

PROSE_MARKER = 'pde.prose-paragraph-marker'
EVALUATED_RULE_IDS = core.EVALUATED_RULE_IDS
REQUIRED_RULE_IDS = frozenset(core.RULE_IDS)
_SEAL = object()
# Issuer-owned chronology survives reflective resetting of bridge attributes.
_ATTEMPTS = weakref.WeakKeyDictionary()
_COMMITTED_BINDINGS = weakref.WeakKeyDictionary()


class CapturedTextUtilsParagraphFixError(TransactionError):
    """Stale/altered authority, coverage, replay, candidate or filesystem state."""


class CapturedTextUtilsParagraphFixInputError(InputError):
    """Initially unsupported grammar, editorial word or required other repair."""


def _require(condition, message):
    if not condition:
        raise CapturedTextUtilsParagraphFixError(message)


def _issued(kind, **values):
    instance = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(instance, name, value)
    return instance


def _retain_authority(value):
    """Original bounded shapes, including the pure evaluator's fixed enums."""
    kind = type(value)
    if isinstance(value, Enum):
        return kind, value
    if kind in (tuple, list, frozenset):
        return kind, tuple(_retain_authority(item) for item in value)
    if kind is MappingProxyType:
        return kind, (value, tuple((_retain_authority(key), _retain_authority(item))
                                  for key, item in value.items()))
    if is_dataclass(value):
        _require(value.__dataclass_params__.frozen, 'Frozen paragraph authority required')
        return kind, tuple((item.name, _retain_authority(getattr(value, item.name)))
                           for item in fields(value))
    return _retain_base(value)


def _matches_authority(value, retained):
    kind, expected = retained
    if type(value) is not kind:
        return False
    if issubclass(kind, Enum):
        return value is expected
    if kind is MappingProxyType:
        original, expected = expected
        if value is not original:
            return False
    if kind in (tuple, list, frozenset, MappingProxyType):
        if len(value) != len(expected):
            return False
        if kind in (tuple, list):
            return all(_matches_authority(item, fact) for item, fact in zip(value, expected))
        if kind is frozenset:
            return all(any(_matches_authority(item, fact) for fact in expected) for item in value)
        return all(_matches_authority(key, key_fact) and _matches_authority(item, item_fact)
                   for (key, item), (key_fact, item_fact) in zip(value.items(), expected))
    if is_dataclass(kind):
        try:
            state = getattr(value, '__dict__', None)
            if state is not None and (type(state) is not dict or len(state) != len(expected)
                                     or any(name not in state for name, _ in expected)):
                return False
            return all(_matches_authority(object.__getattribute__(value, name), fact)
                       for name, fact in expected)
        except AttributeError:
            return False
    return _matches_base(value, retained)


def _retain_original_graph(roots):
    """Pin each original immutable DAG object once, with independent field facts.

    This runs only after full independent semantic verification. Compound edges
    pin identity, while each distinct record's finite original fields are stored
    separately. Shared package nodes are not recursively copied/rechecked many
    times through every replay/history/proof edge on a compiler callback.
    """
    records, seen = [], set()

    def edge(value):
        kind = type(value)
        if kind in (str, bytes, int, bool, type(None), object) or isinstance(value, Enum):
            return False, kind, value
        identity = id(value)
        if identity not in seen:
            seen.add(identity)
            if kind is MappingProxyType:
                expected = tuple((edge(key), edge(item)) for key, item in value.items())
                shape = 'mapping'
            elif kind in (tuple, list, frozenset):
                expected = tuple(edge(item) for item in value)
                shape = 'sequence'
            elif isinstance(value, Path):
                # Match the same bounded original Path backing fields as the
                # established authority helper, including mutable list storage.
                str(value)
                names = ('_raw_paths', '_parts', '_pparts', '_cached_cparts',
                         '_drv', '_root', '_tail_cached', '_str',
                         '_str_normcase_cached', '_parts_normcase_cached', '_hash')
                expected = tuple((name, edge(getattr(value, name)))
                                 for name in names if hasattr(value, name))
                shape = 'fields'
            else:
                _require(is_dataclass(value) and value.__dataclass_params__.frozen,
                         'Original frozen committed record required')
                expected = tuple((item.name, edge(object.__getattribute__(value, item.name)))
                                 for item in fields(value))
                shape = 'fields'
            records.append((value, kind, shape, expected))
        return True, kind, value

    for root in roots:
        edge(root)
    return tuple(records)


def _matches_original_graph(records):
    """Walk only independently retained finite shapes; never refresh a seal."""
    def same(value, fact):
        compound, kind, expected = fact
        if type(value) is not kind:
            return False
        if compound or kind is object or issubclass(kind, Enum):
            return value is expected
        return value == expected

    for value, kind, shape, expected in records:
        if type(value) is not kind:
            return False
        if shape == 'fields':
            try:
                state = getattr(value, '__dict__', None)
                if (not isinstance(value, Path) and state is not None
                        and (type(state) is not dict or len(state) != len(expected)
                             or any(name not in state for name, _ in expected))):
                    return False
                if not all(same(object.__getattribute__(value, name), fact)
                           for name, fact in expected):
                    return False
            except AttributeError:
                return False
        elif shape == 'mapping':
            if len(value) != len(expected) or not all(
                    same(key, key_fact) and same(item, item_fact)
                    for (key, item), (key_fact, item_fact) in zip(value.items(), expected)):
                return False
        elif len(value) != len(expected):
            return False
        elif kind is frozenset:
            if not all(any(same(item, fact) for fact in expected) for item in value):
                return False
        elif not all(same(item, fact) for item, fact in zip(value, expected)):
            return False
    return True


def _exact(value, other):
    if type(value) is MappingProxyType and type(other) is MappingProxyType:
        return _exact(tuple(value.items()), tuple(other.items()))
    return _matches_authority(value, _retain_authority(other))


@dataclass(frozen=True, slots=True)
class AppliedCapitalization:
    path: str
    start: int
    end: int
    replacement: bytes
    rule_id: str = CAPITALIZATION


@dataclass(frozen=True, slots=True)
class CapturedTextUtilsParagraphFixSummary:
    """Historical reporting only. This is not a complete project-analysis claim."""
    profile: str
    target: str
    original_sha256: str
    final_sha256: str
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    applied_edits: tuple[AppliedCapitalization, ...]
    grammar_version: str
    parser_contract: tuple[str, ...]
    config_sha256: str
    catalog_sha256: str
    package_sha256: str
    modified_paths: tuple[str, ...] = ()
    journal_id: str | None = None
    package_files: tuple[tuple[str, str, int], ...] = ()


def _restore_summary(retained):
    kind, expected = retained
    if kind in (str, bytes, int, bool, type(None)):
        return expected
    if kind is tuple:
        return tuple(_restore_summary(item) for item in expected)
    _require(kind in (CapturedTextUtilsParagraphFixSummary, AppliedCapitalization),
             'Invalid retained paragraph summary shape')
    return kind(**{name: _restore_summary(fact) for name, fact in expected})


@dataclass(frozen=True, slots=True)
class _Selection:
    source_profile: str
    grammar_version: str
    parser_contract: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Pass:
    tree: Tree
    results: Mapping
    analysis: GraphAnalysis
    proof: object
    contexts: tuple[Context, ...]


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedTextUtilsParagraphFixCandidate:
    """Only its issuing live bridge may apply this original, unchanged token."""
    sources: Mapping[str, bytes]
    passes: int
    findings: frozenset[str]
    _token: object = field(repr=False)
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Paragraph candidates are issued only by an active bridge')


@dataclass(frozen=True, slots=True)
class _Validation:
    candidate: CapturedTextUtilsParagraphFixCandidate
    replay: object
    proof: object
    history: tuple[_Pass, ...]
    summary: CapturedTextUtilsParagraphFixSummary


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedTextUtilsParagraphCommittedSnapshot:
    """Opaque same-live-bridge postimage, never a bare transaction capability."""
    root: Path
    target: str
    source_profile: str
    grammar_version: str
    parser_contract: tuple[str, ...]
    tree: Tree = field(repr=False)
    sources: Mapping[str, bytes] = field(repr=False)
    evidence: Mapping[str, bytes] = field(repr=False)
    proof: core.CapturedTextUtilsParagraphProof
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    package: PackageSource = field(repr=False)
    manifest: bytes = field(repr=False)
    package_tree: Tree = field(repr=False)
    initializer: InitializerCertificate
    config: object
    catalog: tuple = field(repr=False)
    dependency_evidence: Mapping[str, str]
    config_sha256: str
    catalog_sha256: str
    _receipt: CommittedSnapshot = field(repr=False)
    _validation: _Validation = field(repr=False)
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Committed snapshots require this live paragraph bridge\'s successful apply')


class ProtectedCapturedTextUtilsParagraphFix:
    """Privately borrow exactly the one transaction owned by a new input session.

    The caller supplies its genuine offline-exclusive lease. No existing capture,
    transaction, compiler receipt or serialized proof can be injected.
    """
    def __init__(self, root: Path, target: str, *, package_path: Path,
                 journal_directory: Path, lease, limits=DEFAULT_READ_LIMITS,
                 fault=None, create_journal_directory=False,
                 source_profile=core.SOURCE_PROFILE):
        self._session = CapturedTextUtilsParagraphInputSession(root, target,
            package_path=package_path, journal_directory=journal_directory,
            lease=lease, limits=limits, fault=fault,
            create_journal_directory=create_journal_directory,
            source_profile=source_profile)
        self.target = target
        self.source_profile = source_profile
        self.grammar_version = core.GRAMMAR_VERSION
        self.parser_contract = core.PARSER_CONTRACT
        self._selection = _Selection(source_profile, self.grammar_version, self.parser_contract)
        self._selection_facts = _retain_authority(self._selection)
        self._owner = object()
        self._bridge_identity = weakref.ref(self)
        self._session_identity = weakref.ref(self._session)
        self._active = False
        self._capture = self._tx = self._baseline_graph = self._baseline_proof = None
        self._capture_facts = self._baseline_proof_facts = self._baseline_proof_original = None
        self._candidate = self._validation = self._validation_facts = None
        self._validation_pins = None
        self._applied_validation = self._summary = self._summary_facts = None
        self._history = []
        self._history_facts = ()
        self._committed = self._committed_facts = self._receipt = None
        self._validation_attempt_started = self._apply_attempt_started = False
        _ATTEMPTS[self] = (False, False)

    def _mark_attempt(self, position):
        self._assert_original_identity()
        previous = _ATTEMPTS.get(self)
        _require(type(previous) is tuple and len(previous) == 2,
                 'Original paragraph attempt chronology required')
        _ATTEMPTS[self] = tuple(True if index == position else value
                                for index, value in enumerate(previous))
        if position == 0:
            self._validation_attempt_started = True
        else:
            self._apply_attempt_started = True

    def _assert_publication_pristine(self):
        """Output preparation follows entry admission but precedes every attempt."""
        self._assert_capture_binding()
        tx = self._tx
        _require(_ATTEMPTS.get(self) == (False, False)
                 and self._validation_attempt_started is False
                 and self._apply_attempt_started is False
                 and not self._history and not self._history_facts
                 and all(getattr(self, name) is None for name in (
                     '_candidate', '_validation', '_validation_facts', '_validation_pins',
                     '_applied_validation', '_committed', '_committed_facts', '_receipt',
                     '_summary', '_summary_facts'))
                 and tx._issued is None and tx._issued_facts is None
                 and tx._committed_receipt is None and tx._committed_graph is None
                 and tx._committed_files is None and tx._committed_evidence is None,
                 'Publication preparation must precede every validation or apply attempt')
        self._session.assert_current(self._capture)
        tx._check_active()

    def _borrow_publication_transaction(self):
        """Private pristine borrow of exactly this bridge's original transaction."""
        self._assert_publication_pristine()
        return self._tx

    def _assert_original_identity(self):
        identity = getattr(self, '_bridge_identity', None)
        session_identity = getattr(self, '_session_identity', None)
        _require(type(self) is ProtectedCapturedTextUtilsParagraphFix
                 and type(identity) is weakref.ReferenceType and identity() is self
                 and type(session_identity) is weakref.ReferenceType
                 and session_identity() is getattr(self, '_session', None),
                 'Only the original paragraph bridge may operate its owned session')

    def _assert_selection(self):
        self._assert_original_identity()
        _require(_matches_authority(self._selection, self._selection_facts)
                 and type(getattr(self, 'target', None)) is str
                 and self.target == getattr(self._session, '_target', None)
                 and all(type(getattr(self, name, None)) is type(getattr(self._selection, name))
                         and getattr(self, name) == getattr(self._selection, name)
                         for name in ('source_profile', 'grammar_version', 'parser_contract')),
                 'Paragraph bridge identity, selection or parser contract changed')

    def __enter__(self):
        self._assert_selection()
        self._session.__enter__()
        try:
            self._capture = self._session.capture
            self._tx = self._session._borrow_transaction(self._capture)
            self._baseline_graph = self._tx.snapshot
            self._capture_facts = _retain_authority((self._capture, self._baseline_graph))
            self._active = True
            # Initial refusals are input errors, not forged-candidate authority.
            proof = self._certify(self._capture.tree, initial=True)
            self._baseline_proof = self._baseline_proof_original = proof
            self._baseline_proof_facts = _retain_authority(proof)
            self._analyze_tree(self._capture.tree, initial=True)
            self._session.assert_current(self._capture)
            return self
        except BaseException:
            self._active = False
            self._session.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb):
        # Identity only: a clone cannot close the shared session, while the
        # original must still release locks after any selection/proof refusal.
        self._assert_original_identity()
        self._active = False
        self._candidate = self._validation_facts = self._validation_pins = None
        self._committed_facts = None
        return self._session.__exit__(exc_type, exc, tb)

    def _assert_capture_binding(self):
        self._assert_selection()
        _require(self._active and self._capture_facts is not None
                 and _matches_authority((self._capture, self._baseline_graph), self._capture_facts)
                 and self._tx is self._session._tx,
                 'Paragraph bridge capture changed or expired')
        self._session._assert_binding(self._capture)
        if self._baseline_proof is not None:
            _require(self._baseline_proof is self._baseline_proof_original
                     and self._baseline_proof_facts is not None
                     and _matches_authority(self._baseline_proof, self._baseline_proof_facts),
                     'Original paragraph grammar proof changed')

    def _context(self, graph):
        self._assert_capture_binding()
        baseline = self._baseline_graph
        _require(type(graph) is GraphSnapshot and graph.root is baseline.root
                 and type(graph.sources) is MappingProxyType
                 and len(graph.sources) == 1 and tuple(graph.sources) == (self.target,)
                 and type(graph.sources[self.target]) is bytes
                 and type(graph.evidence) is MappingProxyType
                 and len(graph.evidence) == 4 and tuple(graph.evidence) == tuple(baseline.evidence)
                 and all(graph.evidence[path] is baseline.evidence[path]
                         for path in baseline.evidence),
                 'Paragraph callback must use the exact retained root and four evidence identities')
        return self._capture.catalog

    def _authority(self):
        self._assert_capture_binding()
        capture = self._capture
        initializer = capture.initializer
        _require(type(initializer) is InitializerCertificate
                 and initializer.accepted is True
                 and initializer.import_path == capture.package.import_path == TEXT_UTILS
                 and initializer.source_identity == capture.package.source_identity
                 and initializer.source_sha256 == digest(capture.package.source),
                 'Retained safe-initializer certificate must bind the exact captured package')
        evidence = self._baseline_graph.evidence
        keys = capture.dependency_evidence
        return dict(config_bytes=evidence[keys['config']],
            catalog_bytes=evidence[keys['catalog']], package_source=capture.package,
            manifest_bytes=capture.manifest, package_tree=capture.package_tree,
            source_profile=self._selection.source_profile)

    def _certify(self, tree, *, initial=False):
        authority = self._authority()
        try:
            proof = core.certify(tree, self.target, **authority)
        except core.CapturedTextUtilsParagraphSyntaxError as error:
            if initial:
                raise CapturedTextUtilsParagraphFixInputError(f'Unsupported initial paragraph grammar: {error}') from error
            raise CapturedTextUtilsParagraphFixError(f'Paragraph candidate grammar changed: {error}') from error
        except (InputError, SafetyError) as error:
            raise CapturedTextUtilsParagraphFixError(f'Paragraph proof authority changed: {error}') from error
        try:
            core.verify_proof(proof, tree, self.target, **authority)
        except (InputError, SafetyError) as error:
            raise CapturedTextUtilsParagraphFixError(f'Paragraph proof failed independent verification: {error}') from error
        _require(proof.grammar_version == self._selection.grammar_version
                 and proof.parser_contract == self._selection.parser_contract
                 and tuple(proof.evaluated_rule_ids) == EVALUATED_RULE_IDS
                 and frozenset(proof.not_applicable_rule_ids) == REQUIRED_RULE_IDS - set(EVALUATED_RULE_IDS)
                 and len(proof.structural_reasons) == 28 and len(proof.obligations) == 33
                 and frozenset(rid for rid, reason in proof.structural_reasons if reason)
                     == REQUIRED_RULE_IDS - set(EVALUATED_RULE_IDS),
                 'Paragraph proof must distinguish five actual from 28 structural obligations')
        return proof

    def _compare_candidate(self, tree, proof):
        baseline = self._capture.tree
        if tree.source == baseline.source:
            _require(_exact(tree, baseline) and _exact(proof, self._baseline_proof),
                     'Unedited source must preserve the exact baseline grammar proof')
            return
        try:
            core.compare_candidate(self._baseline_proof, proof,
                before_tree=baseline, after_tree=tree, path=self.target, **self._authority())
        except (InputError, SafetyError) as error:
            raise CapturedTextUtilsParagraphFixError(f'Paragraph candidate ownership changed: {error}') from error

    def _contexts(self, tree, resolution):
        """Independently enumerate every root marker and its first Text owner."""
        nodes, source = tree.nodes, tree.source
        siblings = nodes[0].children
        contexts = []
        for position, index in enumerate(siblings):
            node = nodes[index]
            if (node.kind != 'Ident' or source[node.start:node.end] != b'paragraph_tab'):
                continue
            _require(position > 0, 'Marker is missing its adjacent root Hash')
            hash_node = nodes[siblings[position - 1]]
            _require(hash_node.kind == 'Hash' and hash_node.end == node.start,
                     'Marker must be the exact adjacent root Hash+Ident, never a call')
            binding = resolution.facts[index].binding
            package = self._capture.package
            _require(type(package) is PackageSource and binding is not None
                     and binding.kind == 'api' and binding.name == 'paragraph_tab'
                     and binding.package == TEXT_UTILS
                     and binding.source_identity == package.source_identity
                     and binding.source_sha256 == digest(package.source)
                     and resolution.facts[index].prose_safe
                     and not resolution.facts[index].deferred,
                     'Paragraph marker is not owned by the captured reviewed helper')
            following = position + 1
            if following < len(siblings) and nodes[siblings[following]].kind == 'Space':
                following += 1
            _require(following < len(siblings), 'Marker has no first literal word')
            first_id = siblings[following]
            first = nodes[first_id]
            _require(first.kind == 'Text' and resolution.facts[first_id].prose_safe
                     and not resolution.facts[first_id].deferred
                     and resolution.language_for(first_id) == 'en',
                     'First word must be direct literal English prose')
            word = re.match(rb'[A-Za-z]+', source[first.start:first.end])
            _require(word is not None, 'Marker first word must be an ASCII literal')
            contexts.append(Context(digest(source), self.target,
                3 if source.startswith(b'\xef\xbb\xbf') else 0, len(source),
                hash_node.start, node.end, first.start, first.start + len(word[0]), word[0],
                StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                MarkerBinding.PARAGRAPH_TAB, 'en'))
        _require(bool(contexts), 'At least one owned paragraph marker is required')
        return tuple(contexts)

    @staticmethod
    def _result_shape(result, kind):
        _require(type(result) is kind and all(type(getattr(result, name, None)) is tuple
                 for name in ('diagnostics', 'edits', 'skips', 'parse_errors', 'suppressed')),
                 'Exact frozen actual paragraph rule result required')
        _require(not (result.skips or result.parse_errors or result.suppressed),
                 'Required actual paragraph analysis cannot skip, suppress or fail parsing')

    def _analyze_tree(self, tree, *, initial=False):
        self._assert_capture_binding()
        proof = self._certify(tree, initial=initial)
        # Same-length case edits preserve every exact AST edge/span/operand and
        # all setup/helper/boundary ownership. The proof module checks this too.
        if self._baseline_proof is not None:
            self._compare_candidate(tree, proof)
        resolution = resolve(tree, fallback_language='en', packages=(self._capture.package,))
        _require(not resolution.skips and resolution.source_sha256 == digest(tree.source)
                 and len(resolution.facts) == len(tree.nodes),
                 'Retained package resolver must prove every required source context')
        contexts = self._contexts(tree, resolution)
        _require(_exact(contexts, proof.contexts),
                 'Independent marker/first-word enumeration differs from exhaustive grammar')
        expected_edits, expected_diagnostics, findings = [], [], set()
        for ordinal, context in enumerate(contexts, 1):
            result = evaluate_capitalization(tree.source, context, catalog=self._capture.catalog)
            if result.skip_reason == 'already-uppercase':
                _require(result.edit is None and result.diagnostic is None,
                         'Already-uppercase must actually evaluate clean')
                continue
            if result.skip_reason is not None:
                exception = CapturedTextUtilsParagraphFixInputError if initial else CapturedTextUtilsParagraphFixError
                raise exception(f'Unsupported paragraph initial word: {result.skip_reason}')
            _require(result.edit == Edit(context.first_start, context.first_start + 1,
                                         context.expected_first[:1].upper())
                     and b'a' <= tree.source[context.first_start:context.first_start + 1] <= b'z'
                     and result.diagnostic is not None
                     and result.diagnostic.rule_id == CAPITALIZATION,
                     'Independent capitalization must own exactly one ASCII case byte')
            expected_edits.append(result.edit)
            expected_diagnostics.append(result.diagnostic)
            findings.add(f'{self.target}:{CAPITALIZATION}:paragraph-{ordinal}')
        actual = analyze_paragraphs(tree, self.target, resolution, catalog=self._capture.catalog)
        _require(type(actual) is dict and len(actual) == 3
                 and tuple(actual) == (CAPITALIZATION, MARKER_GAP, MARKER_TARGET),
                 'Actual paragraph rule coverage changed')
        for result in actual.values():
            self._result_shape(result, ParagraphResult)
        script = analyze_script(tree, self.target, _verify=False, catalog=self._capture.catalog)
        self._result_shape(script, ScriptResult)
        boundary = core.analyze_boundaries(tree, self.target, **self._authority())
        self._result_shape(boundary, core.BoundaryResult)
        _require(boundary.source_sha256 == digest(tree.source)
                 and _exact(boundary.boundaries, proof.boundaries),
                 'Actual prose-boundary result differs from independently certified ownership')
        others = (script, actual[MARKER_GAP], actual[MARKER_TARGET], boundary)
        if any(result.diagnostics or result.edits for result in others):
            exception = CapturedTextUtilsParagraphFixInputError if initial else CapturedTextUtilsParagraphFixError
            raise exception('Paragraph slice requires every other actual rule to be clean')
        _require(_exact(actual[CAPITALIZATION].edits, tuple(expected_edits))
                 and _exact(actual[CAPITALIZATION].diagnostics, tuple(expected_diagnostics)),
                 'Actual paragraph proposals differ from independently enumerated word owners')
        _require(not expected_edits or tree.source == self._baseline_graph.sources[self.target],
                 'Paragraph bridge refuses a second repair pass')
        actual_results = {SCRIPT: script, MARKER_GAP: actual[MARKER_GAP],
            MARKER_TARGET: actual[MARKER_TARGET], PROSE_MARKER: boundary,
            CAPITALIZATION: actual[CAPITALIZATION]}
        results = MappingProxyType({rid: actual_results[rid] for rid in EVALUATED_RULE_IDS})
        return results, frozenset(findings), proof, contexts

    def _analyze_graph(self, graph):
        self._context(graph)
        try:
            tree = parse(graph.sources[self.target])
            results, findings, proof, contexts = self._analyze_tree(tree)
        except (AdapterError, SafetyError, InputError) as error:
            raise CapturedTextUtilsParagraphFixError(f'Paragraph callback failed: {error}') from error
        analysis = GraphAnalysis(REQUIRED_RULE_IDS, REQUIRED_RULE_IDS,
            frozenset((self.target,)), frozenset(self._baseline_graph.evidence),
            findings=findings, edits=tuple(SourceEdit(self.target, edit)
                for edit in results[CAPITALIZATION].edits))
        record = _Pass(tree, results, analysis, proof, contexts)
        # Retain only the just-produced genuine bounded pass, before exposing
        # it to any later callback or replay. Never baseline altered history.
        facts = (record, tree, tree.nodes, results, proof, contexts,
                 _retain_authority(record))
        self._history.append(record)
        self._history_facts += (facts,)
        return analysis

    def _assert_history(self):
        history = getattr(self, '_history', None)
        facts = getattr(self, '_history_facts', None)
        _require(type(history) is list and len(history) <= 3
                 and type(facts) is tuple and len(facts) == len(history),
                 'Paragraph actual history changed or exceeded its original bound')
        for record, pins in zip(history, facts):
            _require(record is pins[0] and _matches_authority(record, pins[6]),
                     'Paragraph history tree, proof, contexts, analysis or results changed')
            _require(record.tree is pins[1] and record.tree.nodes is pins[2]
                     and record.results is pins[3] and record.proof is pins[4]
                     and record.contexts is pins[5],
                     'Original paragraph history object identities changed')

    def _transaction_analysis(self, graph):
        """The returned coverage must be this invocation's actual stored pass."""
        _require(type(self._history) is list and len(self._history) < 3,
                 'Paragraph transaction callback exceeded its bounded one-pass history')
        self._assert_history()
        count = len(self._history)
        analysis = self._analyze_graph(graph)
        self._assert_history()
        _require(len(self._history) == count + 1
                 and analysis is self._history[-1].analysis,
                 'Transaction callback substituted its actual source-bound analysis')
        return analysis

    def _check_case_bytes(self, before, after, edits):
        expected = plan(before, edits)
        _require(not expected.deferred and expected.edits == edits
                 and expected.candidate() == after and len(before) == len(after),
                 'Paragraph candidate must replay exact independent source edits')
        for edit in edits:
            _require(type(edit) is Edit and type(edit.start) is int and type(edit.end) is int
                     and edit.end == edit.start + 1 and type(edit.replacement) is bytes
                     and len(edit.replacement) == 1
                     and b'a' <= before[edit.start:edit.end] <= b'z'
                     and edit.replacement == before[edit.start:edit.end].upper(),
                     'Paragraph repair must replace one ASCII lowercase byte by its uppercase')

    def _independent_replay(self):
        self._context(self._baseline_graph)
        tree = parse(self._baseline_graph.sources[self.target])
        initial = self._analyze_tree(tree)[0]

        def analyze(checked):
            return self._analyze_tree(checked)[0]

        def step(before, old, after, new, history):
            _require(not history.deferred and not history.moves,
                     'Paragraph repair cannot defer, insert, delete or move bytes')
            _require(history.edits == old[CAPITALIZATION].edits,
                     'Paragraph replay attribution differs from exact actual proposals')
            self._check_case_bytes(before.source, after.source, history.edits)
            _require(not any(result.diagnostics or result.edits for result in new.values()),
                     'All five actual paragraph rules must be clean after one pass')

        def final(candidate):
            _require(type(candidate.passes) is int and candidate.passes in (0, 1)
                     and tuple(candidate.initial_results) == EVALUATED_RULE_IDS
                     and tuple(candidate.final_results) == EVALUATED_RULE_IDS
                     and not any(result.diagnostics or result.edits
                                 for result in candidate.final_results.values()),
                     'Paragraph independent candidate is not a clean one-pass result')
            _require(all(item.primary_rule_id == CAPITALIZATION
                     and item.rule_ids == (CAPITALIZATION,) and item.contributions
                     and all(c.rule_id == CAPITALIZATION and type(c.pass_number) is int
                             and c.pass_number == 1 for c in item.contributions)
                         for item in candidate.edits),
                     'Paragraph candidate has foreign or unowned rule attribution')
            edits = tuple(item.edit for item in candidate.edits)
            _require(edits == initial[CAPITALIZATION].edits,
                     'Original-source replay differs from independent marker-word proposals')
            self._check_case_bytes(candidate.baseline, candidate.content, edits)

        try:
            replay = converge(tree, initial, analyze, step, final, max_passes=1)
            final(replay)
            return replay
        except (AdapterError, SafetyError, InputError) as error:
            raise CapturedTextUtilsParagraphFixError(f'Independent paragraph replay failed: {error}') from error

    def _check_replay(self, replay):
        try:
            self._check_replay_fields(replay)
        except CapturedTextUtilsParagraphFixError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CapturedTextUtilsParagraphFixError('Returned paragraph replay fields changed') from error

    def _check_replay_fields(self, replay):
        """Check returned replay authority outside its own convergence callback.

        One pass means the complete original-source attributed patch tuple is
        exactly the sole Step's tuple. No composed edit can disappear from the
        successful report or acquire a foreign contribution after return.
        """
        baseline = self._baseline_graph.sources[self.target]
        _require(type(replay) is Candidate and type(replay.passes) is int
                 and replay.passes in (0, 1) and type(replay.steps) is tuple
                 and len(replay.steps) == replay.passes
                 and replay.baseline is baseline and type(replay.content) is bytes
                 and type(replay.edits) is tuple and type(replay.final_tree) is Tree
                 and replay.final_tree.source is replay.content
                 and type(replay.initial_results) is MappingProxyType
                 and type(replay.final_results) is MappingProxyType
                 and len(replay.initial_results) == len(EVALUATED_RULE_IDS)
                 and len(replay.final_results) == len(EVALUATED_RULE_IDS)
                 and tuple(replay.initial_results) == EVALUATED_RULE_IDS
                 and tuple(replay.final_results) == EVALUATED_RULE_IDS,
                 'Independent paragraph replay has altered source/results/step shape')
        _require(bool(self._history)
                 and _exact(replay.initial_results, self._history[0].results)
                 and _exact(replay.final_results, self._history[-1].results),
                 'Returned replay must retain the actual initial/final rule results')
        expected = self._history[0].results[CAPITALIZATION].edits
        _require(len(replay.edits) == len(expected),
                 'Returned original-source attribution omitted or added an edit')
        for item, edit in zip(replay.edits, expected):
            _require(type(item) is AttributedEdit and _exact(item.edit, edit)
                     and item.primary_rule_id == CAPITALIZATION
                     and item.rule_ids == (CAPITALIZATION,)
                     and type(item.contributions) is tuple and len(item.contributions) == 1,
                     'Returned original-source edit or rule attribution changed')
            cause = item.contributions[0]
            _require(type(cause) is Contribution and type(cause.pass_number) is int
                     and cause.pass_number == 1 and cause.rule_id == CAPITALIZATION
                     and _exact(cause.edit, edit),
                     'Returned paragraph contribution is not the sole exact actual proposal')
        if replay.passes:
            step = replay.steps[0]
            _require(type(step) is Step and step.before is baseline
                     and step.after is replay.content and type(step.applied) is tuple
                     and type(step.deferred) is tuple and not step.deferred
                     and type(step.moves) is tuple and not step.moves
                     and _exact(step.applied, replay.edits),
                     'Returned replay step differs from full original-source attribution')
        else:
            _require(not expected and not replay.edits and replay.content is baseline,
                     'A zero-pass paragraph replay must be a genuine exact no-op')
        self._check_case_bytes(baseline, replay.content, expected)
        try:
            checked = parse(replay.content)
        except AdapterError as error:
            raise CapturedTextUtilsParagraphFixError(f'Returned paragraph parser failed: {error}') from error
        _require(_exact(replay.final_tree, checked),
                 'Returned final Tree differs from independent exact parsing')

    def validate(self):
        self._mark_attempt(0)
        self._assert_capture_binding()
        _require(self._applied_validation is None, 'Paragraph bridge cannot be reused after apply')
        self._session.assert_current(self._capture)
        self._candidate = self._validation = self._validation_facts = self._validation_pins = None
        self._history = []
        self._history_facts = ()
        token = self._tx.validate(self._transaction_analysis, required_rules=REQUIRED_RULE_IDS)
        _require(not token.findings and type(token.passes) is int and token.passes in (0, 1),
                 'Paragraph transaction requires a clean zero-or-one-pass candidate')
        _require(type(self._history) is list and len(self._history) == token.passes + 2,
                 'Paragraph transaction must retain exactly its bounded actual source history')
        self._assert_history()
        replay = self._independent_replay()
        self._assert_history()
        self._check_replay(replay)
        baseline = self._baseline_graph.sources[self.target]
        _require(type(replay.passes) is int and replay.passes in (0, 1)
                 and type(replay.steps) is tuple and len(replay.steps) == replay.passes
                 and replay.baseline is baseline and type(replay.content) is bytes
                 and replay.final_tree.source is replay.content
                 and token.sources == {self.target: replay.content} and token.passes == replay.passes,
                 'Transaction and independent paragraph replay disagree')
        expected = ((baseline, replay.content, replay.content) if token.passes else (baseline, baseline))
        _require(tuple(item.tree.source for item in self._history) == expected
                 and _exact(self._history[0].results, replay.initial_results)
                 and all(_exact(item.results, replay.final_results) and not item.analysis.findings
                         for item in self._history[1:]),
                 'Paragraph transaction history differs from independent actual-rule replay')
        if replay.steps:
            _require(tuple(item.edit for item in self._history[0].analysis.edits) == replay.steps[0].edits,
                     'Paragraph transaction edit attribution differs from independent replay')
        proof = self._certify(replay.final_tree)
        self._compare_candidate(replay.final_tree, proof)
        self._session.assert_current(self._capture)
        self._assert_history()
        candidate = _issued(CapturedTextUtilsParagraphFixCandidate, sources=token.sources,
            passes=token.passes, findings=token.findings, _token=token,
            _owner=self._owner, _seal=_SEAL)
        report = self._capture.report
        summary = CapturedTextUtilsParagraphFixSummary(self.source_profile, self.target,
            digest(baseline), digest(replay.content), tuple(sorted(REQUIRED_RULE_IDS)),
            EVALUATED_RULE_IDS, tuple(proof.structural_reasons),
            tuple(AppliedCapitalization(self.target, item.edit.start, item.edit.end,
                                        item.edit.replacement) for item in replay.edits),
            self.grammar_version, self.parser_contract, report.config_sha256,
            report.catalog_sha256, digest(self._capture.package.source),
            package_files=report.package_files)
        validation = _Validation(candidate, replay, proof, tuple(self._history), summary)
        self._candidate, self._validation = candidate, validation
        self._validation_facts = (validation, _retain_authority(validation))
        self._validation_pins = (candidate, token, token.sources, replay, replay.final_tree,
                                 replay.final_tree.nodes, proof, tuple(self._history))
        self._assert_validation(validation)
        return candidate

    def _assert_validation_binding(self, validation):
        """Check original finite facts without rebuilding an identical proof."""
        facts = self._validation_facts
        _require(facts is not None and validation is facts[0]
                 and _matches_authority(validation, facts[1]),
                 'Paragraph candidate, token, replay, proof, report or retained facts changed')
        self._assert_capture_binding()
        pins = self._validation_pins
        _require(pins is not None and validation.candidate is pins[0]
                 and validation.candidate._token is pins[1]
                 and validation.candidate.sources is pins[2]
                 and validation.replay is pins[3] and validation.replay.final_tree is pins[4]
                 and validation.replay.final_tree.nodes is pins[5] and validation.proof is pins[6]
                 and len(validation.history) == len(pins[7])
                 and all(item is original for item, original in zip(validation.history, pins[7]))
                 and validation.candidate._owner is self._owner
                 and validation.candidate._seal is _SEAL
                 and validation.candidate.sources is validation.candidate._token.sources,
                 'Paragraph original candidate/replay/tree/proof identities changed')
    def _assert_validation(self, validation):
        self._assert_validation_binding(validation)
        proof = self._certify(validation.replay.final_tree)
        _require(_exact(proof, validation.proof), 'Retained paragraph proof no longer independently verifies')
        self._compare_candidate(validation.replay.final_tree, validation.proof)

    def apply(self, candidate):
        self._mark_attempt(1)
        _require(self._active and type(candidate) is CapturedTextUtilsParagraphFixCandidate
                 and candidate is self._candidate and self._applied_validation is None,
                 'Only this active paragraph bridge\'s original unused candidate may be applied')
        validation = self._validation
        self._assert_validation(validation)
        self._session.assert_current(self._capture)
        # Copy retained primitive reporting facts before a transaction callback
        # can reflectively mutate exposed frozen report records.
        report = _restore_summary(_retain_authority(validation.summary))
        self._candidate = None
        result = self._tx.apply(candidate._token)
        self._applied_validation = validation
        self._summary = replace(report, modified_paths=result.modified, journal_id=result.journal)
        self._summary_facts = _retain_authority(self._summary)
        # This guard is outside rollback. Honest known success survives later
        # drift; no new baseline is minted and no valid fix is undone.
        self._assert_validation(validation)
        self._tx.assert_current(self._tx.committed_snapshot())
        return result

    @property
    def summary(self):
        _require(self._summary_facts is not None,
                 'A successful paragraph apply is required for historical reporting')
        if not _matches_authority(self._summary, self._summary_facts):
            self._summary = _restore_summary(self._summary_facts)
        return self._summary

    def _assert_committed_binding(self, snapshot):
        binding = _COMMITTED_BINDINGS.get(self)
        _require(binding is not None, 'Original committed paragraph binding required')
        original, validation, capture, baseline, baseline_proof, selection, facts = binding
        _require(self._active and type(snapshot) is CapturedTextUtilsParagraphCommittedSnapshot
                 and snapshot is original is self._committed
                 and self._applied_validation is validation is self._validation
                 and self._capture is capture and self._baseline_graph is baseline
                 and self._baseline_proof is baseline_proof and self._selection is selection
                 and self._committed_facts is facts and _matches_original_graph(facts),
                 'Only this active paragraph bridge\'s unchanged committed snapshot is authoritative')
        # The independently verified semantics and all original nested fields
        # are sealed above, once per distinct object. Never rebuild identical
        # proofs here or call original-baseline filesystem revalidation.
        self._assert_selection()
        self._session._assert_binding(capture)
        _require(self._tx is self._session._tx
                 and snapshot._owner is self._owner and snapshot._seal is _SEAL
                 and snapshot._receipt is self._receipt,
                 'Committed paragraph original provenance changed')

    def committed_snapshot(self):
        validation = self._applied_validation
        _require(self._active and validation is not None,
                 'This paragraph bridge\'s successful independently validated apply is required')
        self._assert_validation(validation)
        if self._committed is not None:
            self._assert_committed_binding(self._committed)
        try:
            receipt = self._tx.committed_snapshot()
        except (AttributeError, TypeError, ValueError) as error:
            raise CapturedTextUtilsParagraphFixError('Committed paragraph transaction receipt changed') from error
        baseline, capture = self._baseline_graph, self._capture
        _require(type(receipt) is CommittedSnapshot and receipt.root is baseline.root
                 and type(receipt.sources) is MappingProxyType
                 and len(receipt.sources) == 1 and tuple(receipt.sources) == (self.target,)
                 and type(receipt.sources[self.target]) is bytes
                 and receipt.sources[self.target] == validation.replay.content
                 and receipt.sources[self.target] == validation.candidate._token.sources[self.target]
                 and validation.replay.final_tree.source is validation.replay.content
                 and type(receipt.evidence) is MappingProxyType
                 and len(receipt.evidence) == 4 and tuple(receipt.evidence) == tuple(baseline.evidence)
                 and all(receipt.evidence[path] is baseline.evidence[path] for path in baseline.evidence),
                 'Committed paragraph postimage/evidence differs from the independently replayed result')
        if self._committed is None:
            self._receipt = receipt
            report = capture.report
            snapshot = _issued(CapturedTextUtilsParagraphCommittedSnapshot,
                root=baseline.root, target=self.target, source_profile=self.source_profile,
                grammar_version=self.grammar_version, parser_contract=self.parser_contract,
                tree=validation.replay.final_tree, sources=receipt.sources, evidence=receipt.evidence,
                proof=validation.proof, required_rule_ids=tuple(sorted(REQUIRED_RULE_IDS)),
                evaluated_rule_ids=EVALUATED_RULE_IDS,
                structural_reasons=tuple(validation.proof.structural_reasons),
                package=capture.package, manifest=capture.manifest, package_tree=capture.package_tree,
                initializer=capture.initializer, config=capture.config, catalog=capture.catalog,
                dependency_evidence=capture.dependency_evidence,
                config_sha256=report.config_sha256, catalog_sha256=report.catalog_sha256,
                _receipt=receipt, _validation=validation, _owner=self._owner, _seal=_SEAL)
            facts = _retain_original_graph((snapshot, validation, capture, baseline,
                                             self._baseline_proof, self._selection))
            self._committed, self._committed_facts = snapshot, facts
            _COMMITTED_BINDINGS[self] = (snapshot, validation, capture, baseline,
                self._baseline_proof, self._selection, facts)
        _require(receipt is self._receipt, 'Committed paragraph transaction generation changed')
        self.assert_committed_current(self._committed)
        return self._committed

    def assert_committed_current(self, snapshot):
        self._assert_committed_binding(snapshot)
        self._tx.assert_current(snapshot._receipt)
