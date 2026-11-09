"""Keep an audit child on the parent's fixed adapter selection and fingerprint."""
import json
from pathlib import Path
import runpy
import sys

import syntax_client


def main():
    if len(sys.argv) < 4 or len(sys.argv[1]) > 8192:
        raise syntax_client.AdapterError('Invalid audit adapter worker request')
    expected = json.loads(sys.argv[1])
    tool = Path(__file__).resolve().parent
    launcher = Path(sys.argv[2])
    if launcher not in (tool / 'typst-lint', tool / 'typst-style') or launcher.resolve() != launcher:
        raise syntax_client.AdapterError('Invalid audit launcher')
    def same_capture():
        current = syntax_client.audit_adapter_inventory()
        if json.dumps(current, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise syntax_client.AdapterError('Audit child adapter differs from parent capture')
    same_capture()
    sys.argv = sys.argv[2:]
    try:
        runpy.run_path(str(launcher), run_name='__main__')
    finally:
        same_capture()


if __name__ == '__main__':
    main()
