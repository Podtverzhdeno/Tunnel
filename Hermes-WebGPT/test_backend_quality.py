"""Regression cases for prompt-wait I/O and terminal schema failures; no external services."""
from contextlib import closing
import concurrent.futures
from fractions import Fraction
import http.client
import json
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import hermes_tool_schema_guard as guard
import relay


def tools(schema):
    return [{'type': 'function', 'function': {'name': 'fixture', 'parameters': schema}}]


def validate(value, schema):
    guard.validate_offered_tool_arguments(tools(schema), 'fixture', value)


def numeric(step):
    return {'type': 'object', 'properties': {'value': {'type': 'number', 'multipleOf': step}},
            'required': ['value'], 'additionalProperties': False}


class NumericTests(unittest.TestCase):
    def test_decimal_multiples_are_accepted(self):
        for value, step in [(0.3, 0.1), (1.2, 0.2), (-0.3, 0.1), (0, 0.1), (1e-12, 1e-13)]:
            with self.subTest(value=value, step=step):
                validate({'value': value}, numeric(step))

    def test_near_multiple_is_not_silently_accepted(self):
        for value in [1.0000000001, 0.9999999999, 1.00000000001]:
            with self.subTest(value=value), self.assertRaises(guard.ToolSchemaError):
                validate({'value': value}, numeric(1))

    def test_invalid_multiple_constraints_raise_typed_errors(self):
        for step in [0, -1, True, False, None, '1', [], float('nan'), float('inf')]:
            with self.subTest(step=step), self.assertRaises(guard.ToolSchemaError):
                validate({'value': 1}, numeric(step))

    def test_large_integer_is_a_valid_finite_number(self):
        validate({'value': 10 ** 400}, {'type': 'object', 'properties': {'value': {'type': 'number'}}})

    def test_large_integer_multiple_keeps_low_digits(self):
        validate({'value': 10 ** 400}, numeric(10))
        with self.assertRaises(guard.ToolSchemaError):
            validate({'value': 10 ** 400 + 1}, numeric(10))

    def test_non_finite_arguments_remain_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.subTest(value=value), self.assertRaises(guard.ToolSchemaError):
                validate({'value': value}, numeric(1))

    def test_malformed_schema_shapes_are_typed_rejections(self):
        cases = [({'required': 3}, {}),
                 ({'type': 'object', 'minProperties': 'invalid'}, {}),
                 ({'type': 'array', 'minItems': None}, {'x': []}),
                 ({'properties': {'x': {'type': 'array', 'minItems': None}}}, {'x': []}),
                 ({'properties': {'x': {'type': 'string', 'minLength': []}}}, {'x': 'a'}),
                 ({'properties': {'x': {'type': 'string', 'pattern': None}}}, {'x': 'a'})]
        # Top-level arguments are always objects; malformed nested schemas are
        # included to exercise paths that used to leak TypeError.
        for schema, value in cases:
            with self.subTest(schema=schema), self.assertRaises(guard.ToolSchemaError):
                validate(value, schema)

    def test_guard_never_mutates_offered_schema_or_arguments(self):
        schema = numeric(0.1)
        before = json.dumps(schema, sort_keys=True)
        args = {'value': 0.3}
        validate(args, schema)
        self.assertEqual(json.dumps(schema, sort_keys=True), before)
        self.assertEqual(args, {'value': 0.3})

    def test_bad_argument_type_stays_rejected(self):
        for value in [True, '1', [], {}]:
            with self.subTest(value=value), self.assertRaises(guard.ToolSchemaError):
                validate({'value': value}, numeric(1))


class QueueStatusTests(unittest.TestCase):
    def setUp(self):
        self.q = relay.Queue(':memory:')
        self.addCleanup(self.q.close)
        self.request = {'model': relay.MODEL, 'messages': [{'role': 'user', 'content': 'private' * 10000}]}
        self.ident = self.q.submit(self.request)

    def test_status_read_has_only_state_and_reason(self):
        row = self.q.read_status(self.ident)
        self.assertEqual(row, {'state': 'queued', 'reason': None})

    def test_status_sql_never_selects_payload_or_credentials(self):
        statements = []
        self.q.db.set_trace_callback(statements.append)
        for _ in range(20):
            self.q.read_status(self.ident)
        selected = [s for s in statements if s.startswith('SELECT')]
        self.assertEqual(len(selected), 20)
        self.assertTrue(all(s.startswith('SELECT state,reason FROM requests') for s in selected))

    def test_unknown_status_id_is_typed_error(self):
        with self.assertRaisesRegex(relay.RelayError, 'unknown_request'):
            self.q.read_status('unknown')

    def test_metadata_tracks_terminal_state_without_payload(self):
        job = self.q.claim(str(uuid.uuid4()))
        self.q.event(job['id'], job['lease'], 'blocked', reason='worker_failed')
        self.assertEqual(self.q.read_status(self.ident)['state'], 'blocked')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.q = relay.Queue(':memory:')
        self.server = relay.Server(('127.0.0.1', 0), self.q, 'synthetic-fixture-key', timeout=2)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.q.close()

    def call(self, path, body=None):
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=4)) as conn:
            headers = {'Authorization': 'Bearer synthetic-fixture-key', 'Content-Type': 'application/json'}
            conn.request('POST' if body is not None else 'GET', path,
                         json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            return response.status, response.read().decode()

    def saved_job(self, schema):
        req = {'model': relay.MODEL, 'messages': [{'role': 'user', 'content': 'synthetic'}], 'tools': tools(schema)}
        ident = self.q.submit(req)
        job = self.q.claim(str(uuid.uuid4()))
        self.q.event(ident, job['lease'], 'intent', 8)
        self.q.event(ident, job['lease'], 'saved', 8, 'https://chatgpt.com/c/test')
        return job

    def test_health_reports_the_loaded_component_fingerprints(self):
        status, raw = self.call('/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['runtime_build'], relay.RUNTIME_BUILD)
        self.assertEqual(len(relay.RUNTIME_BUILD), 4)
        self.assertTrue(all(len(value) == 64 for value in relay.RUNTIME_BUILD.values()))

    def test_health_does_not_relabel_a_running_process_from_changed_disk_files(self):
        with patch.object(relay.Path, 'read_bytes', side_effect=AssertionError('disk read after import')):
            status, raw = self.call('/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['runtime_build'], relay.RUNTIME_BUILD)

    def test_invalid_schema_cannot_leave_a_saved_request_hanging(self):
        job = self.saved_job(numeric(0))
        text = json.dumps({'request_id': job['id'], 'content': None,
                           'tool_calls': [{'name': 'fixture', 'arguments': {'value': 1}}]})
        status, raw = self.call('/bridge/event', {'id': job['id'], 'lease': job['lease'], 'action': 'result',
                                                'tab': 8, 'url': 'https://chatgpt.com/c/test', 'text': text})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)['error']['code'], 'invalid_tool_arguments')
        self.assertEqual(self.q.read(job['id'])['state'], 'blocked')
        self.assertEqual(self.call('/health')[0], 200)

    def test_type_error_in_schema_also_becomes_a_terminal_rejection(self):
        job = self.saved_job({'type': 'object', 'minProperties': None})
        text = json.dumps({'request_id': job['id'], 'content': None,
                           'tool_calls': [{'name': 'fixture', 'arguments': {}}]})
        status, raw = self.call('/bridge/event', {'id': job['id'], 'lease': job['lease'], 'action': 'result',
                                                'tab': 8, 'url': 'https://chatgpt.com/c/test', 'text': text})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)['error']['code'], 'invalid_tool_arguments')
        self.assertEqual(self.q.read(job['id'])['state'], 'blocked')

    def test_waiter_fetches_full_request_only_at_terminal_result(self):
        owner = threading.get_ident()
        reads = []
        original = self.q.read
        def counted(ident):
            if threading.get_ident() != owner:
                reads.append(ident)
            return original(ident)
        self.q.read = counted
        req = {'model': relay.MODEL, 'messages': [{'role': 'user', 'content': 'x' * 80000}]}
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(self.call, '/v1/chat/completions', req)
            job = None
            deadline = time.monotonic() + 1
            while job is None and time.monotonic() < deadline:
                job = self.q.claim(str(uuid.uuid4()))
                if job is None:
                    time.sleep(.005)
            self.assertIsNotNone(job)
            time.sleep(.55)  # Exercise multiple polling iterations in this bounded fixture.
            self.q.event(job['id'], job['lease'], 'intent', 9)
            self.q.event(job['id'], job['lease'], 'saved', 9, 'https://chatgpt.com/c/test')
            reply = json.dumps({'request_id': job['id'], 'content': 'fixture-ok', 'tool_calls': []})
            self.q.event(job['id'], job['lease'], 'result', 9, 'https://chatgpt.com/c/test', reply)
            status, raw = pending.result(timeout=3)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['choices'][0]['message']['content'], 'fixture-ok')
        self.assertEqual(len(reads), 1)


if __name__ == '__main__':
    unittest.main()
