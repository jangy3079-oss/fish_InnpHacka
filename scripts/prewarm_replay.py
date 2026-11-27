"""Warm the running synthetic API and record reproducible timings; no DB writes.

python -m scripts.prewarm_replay --point heatwave_typhoon:0 --point heatwave_typhoon:26
"""
import argparse
import json
import math
from pathlib import Path
import re
from time import perf_counter
from urllib.parse import urlsplit

import httpx


class WarmupError(ValueError):
    pass


def parse_point(value):
    try:
        name, raw = value.split(':')
        hours = float(raw)
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or not math.isfinite(hours) or hours<0:
            raise ValueError
        return name, hours
    except (ValueError, TypeError):
        raise WarmupError('Use scenario:nonnegative_elapsed_hours') from None


def warm(points, client, base_url):
    measurements = []
    config_revision = None
    for name, hours in points:
        started = perf_counter()
        try:
            response = client.post(base_url.rstrip('/')+'/demo/scenario/'+name, params={'t': hours})
            if response.status_code!=200:
                raise WarmupError(f'Synthetic warmup failed: HTTP {response.status_code}; no automatic retry')
            data = response.json()
            revision = data['config_revision']
            effective = data['effective_t']
            if (data['scenario']!=name or type(data['t']) not in (int,float) or data['t']!=hours
                    or type(effective) not in (int,float) or not math.isfinite(effective) or not 0<=effective<=hours or data['simulated'] is not True
                    or data['persisted'] is not False or data['cause_source']!='cause_engine'
                    or not re.fullmatch('[a-f0-9]{64}', revision)
                    or not re.fullmatch('[a-f0-9]{64}', data['scenario_revision'])):
                raise ValueError
            if config_revision is not None and config_revision!=revision:
                raise WarmupError('Configuration changed during warmup; rerun after deployment is stable')
            config_revision = revision
            measurements.append({'scenario': name, 'requested_hours': hours, 'effective_hours': data['effective_t'],
                'seconds': round(perf_counter()-started, 4), 'config_revision': revision,
                'scenario_revision': data['scenario_revision'], 'model_version': data['status']['model_version']})
        except httpx.RequestError:
            raise WarmupError('Synthetic API request failed; no automatic retry') from None
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, WarmupError):
                raise
            raise WarmupError('Invalid synthetic API response') from None
    return {'simulated': True, 'persisted': False, 'measurements': measurements}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--point', action='append', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        parsed = urlsplit(args.base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or parsed.scheme not in ('http','https')
                or parsed.scheme=='http' and parsed.hostname not in ('localhost','127.0.0.1','::1')):
            raise WarmupError('Use HTTPS for remote API, or HTTP on localhost')
        points = [parse_point(p) for p in args.point]
        with httpx.Client(timeout=180, follow_redirects=False) as client:
            result = warm(points, client, args.base_url)
        output = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output+'\n', encoding='utf-8')
        print(output)
        return 0
    except (WarmupError, OSError) as exc:
        print(str(exc) if isinstance(exc, WarmupError) else 'Cannot save warmup report')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
