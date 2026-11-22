"""Independent bytes for boundary CLI/process/native acceptance only.

Canonical sources are authored explicitly, never produced by the linter or by
replaying its proposed edits. No compiler, parser or PDF command runs on import.
"""
from collections import Counter
import base64
import json

from paragraph_fix_test_support import PREFIX

GAP = 'pde.paragraph-marker-gap'
INSERT = 'pde.prose-paragraph-marker'
CASE = 'pde.paragraph-capitalization'
PROTECTED = b'preserves iPhone eBay NASA lowerCase.'

# Match the four established native PDF labels, but exercise both insertion
# and marker-gap repair. Indentation before prose and around markers is data.
MIXED = (PREFIX +
    b'Baseline anchor remains.\n'
    b' \t#paragraph_tab \t\n'
    b'this amber preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b' \tthese bronze preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'\t#paragraph_tab\n'
    b'\twe cobalt preserves iPhone eBay NASA lowerCase.\n'
    b' \t\n'
    b'    observe denim preserves iPhone eBay NASA lowerCase.\n')
MIXED_CANONICAL = (PREFIX +
    b'Baseline anchor remains.\n'
    b'\n'
    b' \t#paragraph_tab \t\n'
    b'This amber preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'#paragraph_tab\n'
    b' \tThese bronze preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'\t#paragraph_tab\n'
    b'\tWe cobalt preserves iPhone eBay NASA lowerCase.\n'
    b' \t\n'
    b'#paragraph_tab\n'
    b'    Observe denim preserves iPhone eBay NASA lowerCase.\n')
UNMARKED = (PREFIX +
    b'Baseline anchor remains.\n'
    b'\n'
    b'this amber preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b' \tthese bronze preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'\twe cobalt preserves iPhone eBay NASA lowerCase.\n'
    b' \t\n'
    b'    observe denim preserves iPhone eBay NASA lowerCase.\n')
UNMARKED_CANONICAL = (PREFIX +
    b'Baseline anchor remains.\n'
    b'\n'
    b'#paragraph_tab\n'
    b'This amber preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'#paragraph_tab\n'
    b' \tThese bronze preserves iPhone eBay NASA lowerCase.\n'
    b'\n'
    b'#paragraph_tab\n'
    b'\tWe cobalt preserves iPhone eBay NASA lowerCase.\n'
    b' \t\n'
    b'#paragraph_tab\n'
    b'    Observe denim preserves iPhone eBay NASA lowerCase.\n')


def source_pair(kind='mixed', newline=b'\n', bom=b''):
    original, expected = ((MIXED, MIXED_CANONICAL) if kind == 'mixed'
                          else (UNMARKED, UNMARKED_CANONICAL))
    return (bom + original.replace(b'\n', newline),
            bom + expected.replace(b'\n', newline))


def expected_rule_counts(kind='mixed'):
    return Counter({GAP: 1, INSERT: 2, CASE: 4}) if kind == 'mixed' else Counter({INSERT: 4, CASE: 4})


def assert_source_journal(test, manifest, target, original, expected):
    data = json.loads(manifest.read_bytes())
    test.assertEqual(data['schema'], 1)
    record, = data['files']
    test.assertEqual((record['path'], base64.b64decode(record['before']),
                     base64.b64decode(record['after'])), (target, original, expected))
    return data


def assert_attributed_repair(test, summary, target, original, expected, *, kind='mixed', changed=True):
    """Replay public baseline envelopes and independently ordered pass edits."""
    test.assertEqual(summary['repair_grammar_version'], 'captured-text-utils-paragraph-repair-grammar-v1')
    test.assertEqual(summary['repair_passes'], 2 if changed else 0)
    edits = summary['applied_edits']
    replay = original
    for edit in sorted(edits, key=lambda item: item['start'], reverse=True):
        test.assertEqual(edit['path'], target)
        test.assertTrue(0 <= edit['start'] <= edit['end'] <= len(original))
        replay = replay[:edit['start']] + edit['replacement'].encode() + replay[edit['end']:]
    test.assertEqual(replay, expected)
    contributions = [part for edit in edits for part in edit['contributions']]
    test.assertEqual(Counter(part['rule_id'] for part in contributions),
                     expected_rule_counts(kind) if changed else Counter())
    for edit in edits:
        test.assertEqual(set(edit['rule_ids']), {part['rule_id'] for part in edit['contributions']})
        test.assertIn(edit['rule_id'], edit['rule_ids'])
    replay = original
    for pass_number in (1, 2):
        parts = [part for part in contributions if part['pass_number'] == pass_number]
        test.assertEqual(Counter(part['rule_id'] for part in parts),
            (Counter({GAP: 1, INSERT: 2, CASE: 2}) if pass_number == 1 else Counter({CASE: 2}))
            if kind == 'mixed' and changed else
            (Counter({INSERT: 4}) if pass_number == 1 else Counter({CASE: 4}))
            if changed else Counter())
        prior = replay
        for part in sorted(parts, key=lambda item: item['start'], reverse=True):
            start, end, raw = part['start'], part['end'], part['replacement'].encode()
            test.assertTrue(0 <= start <= end <= len(prior))
            if part['rule_id'] == CASE:
                test.assertEqual(end - start, 1)
                test.assertEqual(raw, prior[start:end].upper())
                test.assertIn(raw, (b'T', b'W', b'O'))
            elif part['rule_id'] == GAP:
                test.assertEqual(start, end)
                test.assertIn(raw, (b'\n', b'\r\n'))
            else:
                test.assertEqual(part['rule_id'], INSERT)
                test.assertEqual(start, end)
                test.assertIn(raw, (b'#paragraph_tab\n', b'#paragraph_tab\r\n'))
                test.assertNotIn(PROTECTED, raw)
            replay = replay[:start] + raw + replay[end:]
    test.assertEqual(replay, expected)
