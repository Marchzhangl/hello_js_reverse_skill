import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from utils.collector import collect_to_jsonl
from utils.request import RequestClient


class RequestTests(unittest.TestCase):
    def test_cookie_defaults_and_no_retry_of_post(self):
        client=RequestClient()
        client.set_cookie('demo','value')
        self.assertEqual(client.session.cookies.get('demo'),'value')
        client.session.request=Mock(side_effect=requests.Timeout())
        with self.assertRaises(requests.Timeout): client.post('https://example.test')
        self.assertEqual(client.session.request.call_count,1)
        self.assertEqual(client.session.request.call_args.kwargs['timeout'],30)

    def test_get_retry_and_rejected_status(self):
        client=RequestClient(retry_delay=0)
        response=Mock(status_code=200)
        client.session.request=Mock(side_effect=[requests.ConnectionError(),response])
        with patch('utils.request.time.sleep'):
            self.assertIs(client.get('https://example.test'),response)
        response=Mock(status_code=403)
        response.raise_for_status.side_effect=requests.HTTPError('403')
        client.session.request=Mock(return_value=response)
        with self.assertRaises(requests.HTTPError): client.get('https://example.test')
        self.assertEqual(client.session.request.call_count,1)


class CollectorTests(unittest.TestCase):
    def test_resume_deduplicates_and_recovers_uncheckpointed_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'data.jsonl'
            calls=[]
            def fetch(cursor):
                calls.append(cursor)
                return {1:[{'id':1},{'id':2}],2:[{'id':2},{'id':3}]}[cursor]
            args=dict(fetch_page=fetch,extract_items=lambda p:p,
                      next_cursor=lambda p,c:c+1 if c==1 else None,
                      output_path=output,job_key='fixture',item_key=lambda x:x['id'])
            self.assertEqual(collect_to_jsonl(**args,max_pages=1)['status'],'limited')
            with output.open('ab') as stream: stream.write(b'{"partial":')
            result=collect_to_jsonl(**args,max_pages=2,resume=True)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(calls,[1,2])
            self.assertEqual([json.loads(x)['id'] for x in output.read_text().splitlines()],[1,2,3])
            collect_to_jsonl(**args,resume=True)
            self.assertEqual(calls,[1,2])

    def test_failed_fetch_and_schema_do_not_advance_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'data.jsonl'
            args=dict(extract_items=lambda p:p,next_cursor=lambda p,c:None,
                      output_path=output,job_key='fixture')
            with self.assertRaises(ValueError):
                collect_to_jsonl(lambda c: {'error':'not a list'},**args)
            state=json.loads(Path(str(output)+'.checkpoint.json').read_text())
            self.assertEqual(state['pages'],0)
            self.assertEqual(output.read_bytes(),b'')
            self.assertEqual(collect_to_jsonl(lambda c:[1],**args,resume=True)['items'],1)

    def test_cursor_loop_job_mismatch_and_modified_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'data.jsonl'
            args=dict(fetch_page=lambda c:[c],extract_items=lambda p:p,
                      next_cursor=lambda p,c:c,output_path=output,job_key='fixture')
            with self.assertRaises(ValueError): collect_to_jsonl(**args)
            args['next_cursor']=lambda p,c:c+1
            collect_to_jsonl(**args,resume=True,max_pages=1)
            args['job_key']='different'
            with self.assertRaises(ValueError): collect_to_jsonl(**args,resume=True)
            args['job_key']='fixture'
            output.write_bytes(b'x\n')
            with self.assertRaises(ValueError): collect_to_jsonl(**args,resume=True)

    def test_refuses_overwrite_and_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'data.jsonl'
            args=dict(fetch_page=lambda c:[],extract_items=lambda p:p,next_cursor=lambda p,c:None,
                      output_path=output,job_key='fixture')
            lock=Path(str(output)+'.lock');lock.touch()
            with self.assertRaises(FileExistsError): collect_to_jsonl(**args)
            lock.unlink();output.write_text('user data')
            with self.assertRaises(FileExistsError): collect_to_jsonl(**args)
            self.assertEqual(output.read_text(),'user data')


if __name__=='__main__': unittest.main()
