"""Compose three source-only previews over one retained document source.

Every stage uses the previous candidate bytes; this is not a write transaction
or a compiler snapshot. A failure withholds the complete per-source sequence.
"""
from document_display import RULE_ID as GAP, STANDALONE, analyze_all
from document_gap_preview import _scopes, preview as preview_gap
from document_standalone_preview import preview as preview_standalone
from edit_plan import Edit, plan
import include_context
from script_rule import RULE_ID as SCRIPT, analyze as analyze_script
from syntax_client import parse
from types import MappingProxyType
from typst_lint import InputError, SafetyError, digest


def preview(tree, path, catalog):
    """Return hash-chained edits or a withheld result for this source."""
    baseline = tree.source
    current = tree
    steps = []

    def apply_display(function, rule_id):
        nonlocal current
        result = function(current, path, catalog)
        if result is None:
            return
        if result['verification'] == 'withheld':
            raise SafetyError(f"{rule_id}: {result['reason']}")
        edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                      for row in result['edits'])
        candidate = plan(current.source, edits).candidate()
        if digest(current.source) != result['baseline_sha256'] or digest(candidate) != result['candidate_sha256']:
            raise SafetyError(f'{rule_id}: preview hash mismatch')
        steps.append(dict(rule_id=rule_id, baseline_sha256=digest(current.source),
                          candidate_sha256=digest(candidate), edits=result['edits']))
        current = parse(candidate, tree.mode)

    try:
        apply_display(preview_standalone, STANDALONE)
        script = analyze_script(current, path, catalog=catalog, _verify=False, _allow_code_call=True)
        if script.skips or script.parse_errors:
            raise SafetyError('Script analysis is incomplete: ' + '; '.join(script.skips))
        if script.edits:
            proposal = plan(current.source, script.edits)
            if proposal.deferred or len(proposal.edits) != len(script.edits):
                raise SafetyError('Conflicting or ineffective script edits')
            candidate = proposal.candidate()
            candidate_tree = parse(candidate, tree.mode)
            final = analyze_script(candidate_tree, path, catalog=catalog, _verify=False, _allow_code_call=True)
            if (candidate_tree.nodes[0].errors or final.parse_errors or final.skips or final.edits
                    or final.diagnostics or len(final.suppressed) != len(script.suppressed)):
                raise SafetyError('Script candidate failed fresh parse and reanalysis')
            if _scopes(current, catalog, proposal.edits) != _scopes(candidate_tree, catalog):
                raise SafetyError('Script candidate changes an inline directive target')
            steps.append(dict(rule_id=SCRIPT, baseline_sha256=digest(current.source),
                              candidate_sha256=digest(candidate),
                              edits=[dict(start=e.start, end=e.end,
                                          replacement=e.replacement.decode('utf-8'), rule_id=SCRIPT)
                                     for e in proposal.edits]))
            current = candidate_tree
        apply_display(preview_gap, GAP)
        final_displays = analyze_all(current, path, catalog=catalog)
        final_scripts = analyze_script(current, path, catalog=catalog, _verify=False, _allow_code_call=True)
        if (current.nodes[0].errors or final_scripts.parse_errors or final_scripts.skips
                or final_scripts.diagnostics or final_scripts.edits
                or any(value.diagnostics for value in final_displays.values())):
            raise SafetyError('Final candidate failed the three selected source checks')
        if not steps:
            return None
        return dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=digest(current.source),
                    verification='hash-chained-parse-and-three-rule-reanalysis', steps=steps,
                    unresolved_display_contexts={rid: len(value.skips) for rid, value in final_displays.items()})
    except (InputError, SafetyError, OSError, ValueError) as error:
        return dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=None,
                    verification='withheld', steps=[], reason=str(error))


def _same_data(expected, supplied):
    """Compare nested report data without treating booleans as integers."""
    if type(expected) is not type(supplied):
        return False
    if type(expected) is dict:
        return (expected.keys() == supplied.keys()
                and all(_same_data(value, supplied[key]) for key, value in expected.items()))
    if type(expected) is list:
        return len(expected) == len(supplied) and all(_same_data(a, b) for a, b in zip(expected, supplied))
    return expected == supplied


def replay(tree, path, catalog, record):
    """Recompute and replay a sequence in memory for a downstream consumer.

    Serialized plans are data, not authority: reject stale or altered records and
    check the hash chain again as edits are applied. The returned bytes are not a
    protected write receipt or a compiler resource snapshot.
    """
    expected = preview(tree, path, catalog)
    return _replay_expected(tree, record, expected)


def _replay_expected(tree, record, expected):
    if (type(record) is not dict or expected is None or not _same_data(expected, record)
            or expected['verification'] != 'hash-chained-parse-and-three-rule-reanalysis'):
        raise SafetyError('Sequence is stale, altered, incomplete or withheld')
    current = tree.source
    for step in record['steps']:
        if digest(current) != step['baseline_sha256']:
            raise SafetyError('Sequence baseline hash mismatch')
        edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                      for row in step['edits'])
        proposal = plan(current, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Sequence contains conflicting or ineffective edits')
        current = proposal.candidate()
        if digest(current) != step['candidate_sha256']:
            raise SafetyError('Sequence candidate hash mismatch')
    if digest(current) != record['candidate_sha256']:
        raise SafetyError('Sequence final hash mismatch')
    return current


def replay_document(context, records, catalog):
    """Derive an immutable in-memory source map from one issued include capture.

    Every captured source must have exactly its current sequence record or no
    record when no changes are needed. Any withheld, omitted, duplicate or
    foreign record refuses the whole map. Includes establish no import/resource
    closure and the returned mapping is not compiler or write authority.
    """
    include_context.assert_include_context(context)
    if type(records) is not list or len(records) > len(context.sources):
        raise SafetyError('Invalid document sequence record set')
    indexed = {}
    for record in records:
        if type(record) is not dict or type(record.get('path')) is not str or record['path'] in indexed:
            raise SafetyError('Invalid or duplicate document sequence path')
        indexed[record['path']] = record
    if not set(indexed).issubset({source.path for source in context.sources}):
        raise SafetyError('Document sequence contains a foreign path')
    candidates = {}
    for source in context.sources:
        expected = preview(source.tree, source.path, catalog)
        record = indexed.get(source.path)
        if expected is None and record is None:
            candidates[source.path] = source.source
        else:
            candidates[source.path] = _replay_expected(source.tree, record, expected)
    include_context.assert_include_context(context)
    return MappingProxyType(candidates)
