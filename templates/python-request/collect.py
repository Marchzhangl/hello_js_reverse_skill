"""Configurable JSON API collector. Run: python collect.py --config job.json.

For custom signatures, import collect_to_jsonl and supply fetch_page directly;
see references/general-collection.md in the Skill repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
from utils.collector import collect_to_jsonl
from utils.request import RequestClient


def at_path(value, path):
    for key in path.split('.') if path else []:
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def run(config, output, resume=False):
    pagination = config.get('pagination', {})
    mode = pagination.get('mode', 'page')
    if mode not in ('page', 'offset', 'cursor', 'none'):
        raise ValueError('pagination.mode must be page/offset/cursor/none')
    if mode == 'cursor' and not pagination.get('next_path'):
        raise ValueError('cursor pagination requires next_path')
    client = RequestClient(headers=config.get('headers'), cookies=config.get('cookies'),
                           timeout=config.get('timeout', 30))
    def fetch(cursor):
        params, body = dict(config.get('params', {})), dict(config.get('body', {}))
        target = body if pagination.get('in') == 'body' else params
        if mode != 'none' and cursor is not None:
            target[pagination.get('param', 'page' if mode == 'page' else mode)] = cursor
        kwargs = {'params': params}
        if config.get('method', 'GET').upper() != 'GET':
            kwargs['json'] = body
        payload = client.request(config.get('method', 'GET'), config['url'], **kwargs).json()
        if 'success_path' in config and at_path(payload, config['success_path']) != config['success_value']:
            raise ValueError('API business status failed; checkpoint was not advanced')
        return payload
    def following(payload, cursor):
        if mode == 'none':
            return None
        if mode == 'cursor':
            token = at_path(payload, pagination['next_path'])
            return None if token is None or token == '' else token
        return cursor + pagination.get('step', 1)
    # Store only a digest of configuration, never headers/cookies themselves.
    # Exclude refreshable credentials and allow a larger page bound on resume.
    identity = {k:v for k,v in config.items() if k not in ('headers','cookies','max_pages','timeout')}
    job_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    try:
        return collect_to_jsonl(fetch, lambda value: at_path(value, config.get('items_path', 'data')),
            following, output_path=output, job_key=job_key,
            start_cursor=pagination.get('start', None if mode == 'cursor' else 0 if mode == 'offset' else 1),
            max_pages=config.get('max_pages', 100), resume=resume,
            item_key=(lambda item: at_path(item, config['item_key'])) if config.get('item_key') else None)
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Collect a configured JSON API into resumable JSONL')
    parser.add_argument('--config', required=True)
    parser.add_argument('--output', default='artifacts/data.jsonl')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    result = run(json.loads(Path(args.config).read_text(encoding='utf-8')), args.output, args.resume)
    print(json.dumps(result, ensure_ascii=False))
