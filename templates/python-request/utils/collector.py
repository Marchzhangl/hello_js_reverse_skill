"""Generic bounded page/cursor collection with recoverable JSONL checkpoints.

fetch_page(cursor) owns HTTP, authentication and signing. extract_items(payload)
returns a list, and next_cursor(payload, cursor) returns the next token or None.
Only one process may write a job at a time. A stale .lock after a hard crash must
be removed by the operator after confirming that the previous writer is gone.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path


def _token(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _atomic_json(path, data):
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name+'.', delete=False) as stream:
            temp_name = stream.name
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def collect_to_jsonl(fetch_page, extract_items, next_cursor, *, output_path,
                     job_key, start_cursor=1, max_pages=100, item_key=None,
                     checkpoint_path=None, resume=False):
    """Write complete pages and checkpoint after durable output.

    max_pages is the total job bound, including resumed pages. item_key is an
    optional callable for deduplication across pages/runs; missing keys must be
    rejected by that callable. job_key identifies endpoint/query/signer semantics
    and must change when those change. Never put credentials in job_key.
    An empty list or next_cursor=None completes the job; hitting max_pages gives
    status=limited, permitting a later resume with a higher explicit bound.
    Seen cursors/keys are retained in the checkpoint, intended for bounded jobs.
    """
    if not isinstance(max_pages, int) or max_pages < 1 or not job_key:
        raise ValueError('max_pages must be positive and job_key must be non-empty')
    output = Path(output_path).resolve()
    checkpoint = Path(checkpoint_path).resolve() if checkpoint_path else output.with_suffix(output.suffix+'.checkpoint.json')
    if output == checkpoint:
        raise ValueError('output and checkpoint must be different files')
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_suffix(output.suffix+'.lock')
    if checkpoint == lock:
        raise ValueError('checkpoint cannot use the writer lock path')
    with lock.open('x'):
        pass
    try:
        state = {'schema_version':1, 'job_key':job_key, 'output':str(output),
                 'next_cursor':start_cursor, 'pages':0, 'items':0, 'offset':0,
                 'sha256':hashlib.sha256(b'').hexdigest(), 'complete':False,
                 'seen_cursors':[], 'seen_keys':[], 'deduplicate':item_key is not None}
        digest = hashlib.sha256()
        if resume:
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            for key in ('schema_version','job_key','output','deduplicate'):
                if saved.get(key) != state[key]:
                    raise ValueError('checkpoint does not match this job: '+key)
            state = saved
            with output.open('rb') as stream:
                remaining = state['offset']
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('output is shorter than its checkpoint')
                    digest.update(chunk)
                    remaining -= len(chunk)
            if digest.hexdigest() != state['sha256']:
                raise ValueError('output prefix has changed since checkpoint')
            if state['complete']:
                return {'status':'complete','pages':state['pages'],'items':state['items'],'output':str(output)}
            mode = 'r+b'
        else:
            if checkpoint.exists():
                raise FileExistsError('checkpoint already exists; use resume or another output')
            mode = 'x+b'
        seen_cursors, seen_keys = set(state['seen_cursors']), set(state['seen_keys'])
        with output.open(mode) as stream:
            stream.truncate(state['offset'])
            stream.seek(state['offset'])
            if not resume:
                _atomic_json(checkpoint, state)
            while state['pages'] < max_pages and not state['complete']:
                cursor = state['next_cursor']
                cursor_key = _token(cursor)
                if cursor_key in seen_cursors:
                    raise ValueError('pagination cursor repeated; no request was replayed')
                payload = fetch_page(cursor)
                items = extract_items(payload)
                if not isinstance(items, list):
                    raise ValueError('extract_items must return a list; validate business errors before extraction')
                following = next_cursor(payload, cursor) if items else None
                if following is not None and (_token(following) == cursor_key or _token(following) in seen_cursors):
                    raise ValueError('pagination cursor cycle detected')
                # Validate/serialize a whole page before mutating its checkpoint.
                fresh, new_keys = [], set()
                for item in items:
                    if item_key:
                        key = _token(item_key(item))
                        if key in seen_keys or key in new_keys:
                            continue
                        new_keys.add(key)
                    fresh.append((_token(item)+'\n').encode('utf-8'))
                for line in fresh:
                    stream.write(line)
                    digest.update(line)
                stream.flush()
                os.fsync(stream.fileno())
                seen_cursors.add(cursor_key)
                seen_keys.update(new_keys)
                state.update(next_cursor=following, pages=state['pages']+1,
                             items=state['items']+len(fresh), offset=stream.tell(),
                             sha256=digest.hexdigest(), complete=following is None,
                             seen_cursors=sorted(seen_cursors), seen_keys=sorted(seen_keys))
                _atomic_json(checkpoint, state)
        return {'status':'complete' if state['complete'] else 'limited',
                'pages':state['pages'],'items':state['items'],'output':str(output),
                'checkpoint':str(checkpoint)}
    finally:
        lock.unlink()
