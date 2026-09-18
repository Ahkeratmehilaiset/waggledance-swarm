"""Prepare role requests/handoffs or report observed latency; no bridge writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_workflow import prepare_request, prepare_handoff, latency_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'handoff', 'latency'))
    parser.add_argument('--input', required=True, type=Path, help='JSON plan or request/reply/next_plan or request/observations/target')
    parser.add_argument('--telemetry-directory', type=Path, help='For latency: read stage-*.json observations from shared/telemetry')
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding='utf-8-sig'))
        if args.action == 'prepare': result = prepare_request(data)
        elif args.action == 'handoff': result = prepare_handoff(data['request'], data['reply'], data['next_plan'])
        else:
            observations = data.get('observations', [])
            if args.telemetry_directory:
                observations = [json.loads(p.read_text(encoding='utf-8-sig'))
                                for p in args.telemetry_directory.glob('stage-*.json')]
            result = latency_report(data['request'], observations, target=data['target'])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
