"""Policy tests use only temporary projects/databases, never the owner ledger."""
import io
import json
import sqlite3
import os
import subprocess
import threading
import tempfile
import shutil
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import spec_tools as spec
from spec_approval_queue import ApprovalQueue, QueueError
import test_spec_approval_queue as legacy
from test_spec_approval_queue import Terminal


class ProjectPolicyTests(unittest.TestCase):
    setUp = legacy.ApprovalQueueTests.setUp
    prepare = legacy.ApprovalQueueTests.prepare
    approve = legacy.ApprovalQueueTests.approve
    def policy(self, action='enable', project='example-project', **options):
        return self.queue.project_policy_console(project, action,
            stdin=Terminal(action.upper() + ' ' + project + '\n'),
            stdout=Terminal(), **options)

    def assert_code(self, code, operation):
        with self.assertRaises(QueueError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_policy_default_off(self):
        status = self.queue.project_policy_status('example-project')
        self.assertFalse(status['configured'])
        self.assertFalse(status['enabled'])
        self.assertEqual(status['operation'], 'spec_create')

    def test_policy_requires_both_terminal_streams(self):
        for ins, out in ((io.StringIO(), Terminal()), (Terminal(), io.StringIO())):
            self.assert_code('INTERACTIVE_REQUIRED', lambda: self.queue.project_policy_console(
                'example-project', 'enable', stdin=ins, stdout=out))
        self.assertFalse(self.queue.project_policy_status('example-project')['enabled'])

    def test_policy_wrong_confirmation_no_mutation(self):
        result = self.queue.project_policy_console('example-project', 'enable',
            stdin=Terminal('ENABLE other-project\n'), stdout=Terminal())
        self.assertFalse(result['changed'])
        self.assertFalse(self.queue.project_policy_status('example-project')['configured'])

    def test_policy_default_duration_and_persistence(self):
        result = self.policy()
        self.assertTrue(result['enabled'])
        self.assertEqual(result['expires_at'], self.now + 24 * 3600)
        restarted = ApprovalQueue(self.queue.db_path, self.root, clock=lambda: self.now)
        self.assertEqual(restarted.project_policy_status('example-project'),
                         self.queue.project_policy_status('example-project'))

    def test_policy_duration_and_permanent(self):
        self.assertEqual(self.policy(hours=.5)['expires_at'], self.now + 1800)
        self.assertIsNone(self.policy(permanent=True)['expires_at'])
        self.now += 40000000
        self.assertTrue(self.queue.project_policy_status('example-project')['enabled'])

    def test_policy_invalid_options(self):
        for hours in (False, 0, -1, 8761, float('nan'), float('inf'), '24'):
            with self.subTest(hours=hours):
                self.assert_code('POLICY_EXPIRY_INVALID', lambda: self.policy(hours=hours))
        self.assert_code('POLICY_EXPIRY_INVALID', lambda: self.policy(permanent=True, hours=1))
        self.assert_code('POLICY_EXPIRY_INVALID', lambda: self.policy('disable', hours=1))
        self.assert_code('POLICY_ACTION_INVALID', lambda: self.policy('unknown'))

    def test_policy_exact_project_no_sibling_or_child_inheritance(self):
        self.policy()
        for project in ('sibling', 'example-project/child'):
            (self.root / project).mkdir()
            brief = spec.read_manifest(self.manifest, self.root)
            brief['project'] = project
            name = project.replace('/', '-') + '.json'
            (self.root / name).write_text(json.dumps(brief), encoding='utf-8')
            digest = spec.preview(brief, self.root)['plan_sha256']
            self.assertEqual(self.queue.prepare(name, digest)['state'], 'pending')
            self.assertFalse(self.queue.project_policy_status(project)['enabled'])

    def test_auto_prepare_is_rollback_safe_and_read_only(self):
        self.policy()
        result = self.queue.prepare(self.manifest, self.plan_hash)
        self.assertEqual(result['state'], 'approved')
        self.assertEqual(result['approval_source'], 'project_policy')
        self.assertFalse((self.root / 'example-project/openspec').exists())
        with closing(self.queue._connect()) as db:
            state = db.execute('SELECT state FROM spec_requests WHERE request_id=?',
                               (result['request_id'],)).fetchone()[0]
        self.assertEqual(state, 'pending')  # Older broker refuses this state.

    def test_auto_execute_once_and_replay_after_revocation(self):
        self.policy()
        request = self.prepare()
        with patch('spec_approval_queue.spec.create', return_value={
                'files': ['a.md'], 'upstream': {'validated': True}}) as create:
            self.assertTrue(self.queue.execute(request)['created'])
            self.policy('disable')
            self.assertTrue(self.queue.execute(request)['replayed'])
            create.assert_called_once()

    def test_disable_revokes_unstarted_request_and_future_prepare(self):
        self.policy()
        request = self.prepare()
        self.policy('disable')
        self.assertEqual(self.queue.status(request)['state'], 'revoked')
        with patch('spec_approval_queue.spec.create') as create:
            self.assert_code('PROJECT_APPROVAL_REVOKED', lambda: self.queue.execute(request))
            create.assert_not_called()
        self.assertEqual(self.queue.status(self.prepare())['state'], 'pending')

    def test_reenable_does_not_revive_old_binding(self):
        grant = self.policy()['grant_id']
        request = self.prepare()
        self.assertNotEqual(self.policy()['grant_id'], grant)
        self.assertEqual(self.queue.status(request)['state'], 'revoked')
        self.assertEqual(self.queue.status(self.prepare())['state'], 'approved')

    def test_policy_not_retroactive(self):
        request = self.prepare()
        self.policy()
        self.assertEqual(self.queue.status(request)['state'], 'pending')
        self.assert_code('APPROVAL_REQUIRED', lambda: self.queue.execute(request))

    def test_policy_expiry_and_clock_rollback(self):
        self.policy(hours=.01)
        request = self.prepare()
        self.now -= 1
        self.assertFalse(self.queue.project_policy_status('example-project')['enabled'])
        self.assert_code('PROJECT_APPROVAL_REVOKED', lambda: self.queue.execute(request))
        self.now += 38
        self.assertFalse(self.queue.project_policy_status('example-project')['enabled'])
        self.assertEqual(self.queue.status(request)['state'], 'revoked')

    def test_request_ttl_still_enforced_with_permanent_policy(self):
        self.policy(permanent=True)
        request = self.prepare()
        self.now += 901
        self.assertEqual(self.queue.status(request)['state'], 'expired')
        self.assert_code('APPROVAL_EXPIRED', lambda: self.queue.execute(request))

    def test_expired_revoked_request_reports_expired(self):
        self.policy()
        request = self.prepare()
        self.policy('disable')
        self.now += 901
        self.assertEqual(self.queue.status(request)['state'], 'expired')

    def test_manual_denial_supersedes_policy(self):
        self.policy()
        request = self.prepare()
        self.approve(request, False)
        self.policy()
        self.assertEqual(self.queue.status(request)['state'], 'denied')
        self.assert_code('APPROVAL_REQUIRED', lambda: self.queue.execute(request))

    def test_explicit_manual_approval_survives_policy_revocation(self):
        self.policy()
        request = self.prepare()
        self.approve(request)
        self.policy('disable')
        self.assertEqual(self.queue.status(request)['state'], 'approved')
        with patch('spec_approval_queue.spec.create', return_value={
                'files': [], 'upstream': {'validated': True}}):
            self.assertTrue(self.queue.execute(request)['created'])

    def test_auto_manifest_mutation_blocks_without_writes(self):
        self.policy()
        request = self.prepare()
        source = self.root / self.manifest
        source.write_text(source.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        with patch('spec_approval_queue.spec.create') as create:
            self.assert_code('PREVIEW_CHANGED', lambda: self.queue.execute(request))
            create.assert_not_called()

    def test_auto_failure_durable_not_retried_or_revived(self):
        self.policy()
        request = self.prepare()
        with patch('spec_approval_queue.spec.create', side_effect=OSError('private detail')) as create:
            self.assert_code('CREATE_FAILED', lambda: self.queue.execute(request))
            self.policy()
            self.assert_code('APPROVAL_REQUIRED', lambda: self.queue.execute(request))
            create.assert_called_once()
        self.assertEqual(self.queue.status(request)['state'], 'failed')

    def test_directory_replacement_invalidates_policy(self):
        self.policy()
        request = self.prepare()
        (self.root / 'example-project').rename(self.root / 'old-project')
        (self.root / 'example-project').mkdir()
        self.assertFalse(self.queue.project_policy_status('example-project')['enabled'])
        self.assert_code('PROJECT_APPROVAL_REVOKED', lambda: self.queue.execute(request))

    def test_alias_not_eligible_even_inside_workspace(self):
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.root / 'example-project', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink privilege unavailable; separate Windows junction test required')
        self.assert_code('PROJECT_ALIAS_DENIED', lambda: self.policy(project='alias'))

    def test_invalid_paths_and_workspace_root_rejected(self):
        for name in ('..', '.', '../outside', '/tmp', 'C:/Windows'):
            with self.subTest(name=name), self.assertRaises((spec.SpecError, QueueError)):
                self.policy(project=name)

    def test_directory_change_during_confirmation_rejected(self):
        queue = self.queue
        root = self.root
        class ReplacingTerminal(Terminal):
            def readline(self):
                (root / 'example-project').rename(root / 'old-project')
                (root / 'example-project').mkdir()
                return super().readline()
        self.assert_code('PROJECT_CHANGED', lambda: queue.project_policy_console(
            'example-project', 'enable', stdin=ReplacingTerminal('ENABLE example-project\n'), stdout=Terminal()))
        self.assertFalse(queue.project_policy_status('example-project')['configured'])

    def test_audit_contains_metadata_only(self):
        self.policy()
        request = self.prepare()
        with patch('spec_approval_queue.spec.create', return_value={
                'files': [], 'upstream': {'validated': True}}):
            self.queue.execute(request)
        self.policy('disable')
        with closing(self.queue._connect()) as db:
            entries = db.execute('SELECT action,request_id FROM spec_project_policy_audit ORDER BY event_id').fetchall()
            columns = [c[1] for c in db.execute('PRAGMA table_info(spec_project_policy_audit)')]
        self.assertEqual([e[0] for e in entries], ['enable','auto_scope_bound','auto_execution_started','disable'])
        self.assertEqual(entries[1][1], request)
        self.assertEqual(columns, ['event_id','timestamp','project_path','action','grant_id','request_id'])

    def test_additive_migration_preserves_manual_approval(self):
        request = self.prepare()
        self.approve(request)
        with closing(self.queue._connect()) as db:
            before = db.execute('SELECT * FROM spec_requests').fetchall()
            for table in ('spec_request_project_policy','spec_project_policy_audit','spec_project_policies'):
                db.execute('DROP TABLE ' + table)
        restarted = ApprovalQueue(self.queue.db_path, self.root, clock=lambda: self.now)
        with closing(restarted._connect()) as db:
            self.assertEqual(before, db.execute('SELECT * FROM spec_requests').fetchall())
        self.assertEqual(restarted.status(request)['state'], 'approved')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction API')
    def test_windows_junction_rejected(self):
        alias = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias),
                                 str(self.root / 'example-project')], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            self.assert_code('PROJECT_ALIAS_DENIED', lambda: self.policy(project='junction'))
        finally:
            alias.rmdir()

    def test_parallel_execution_starts_only_once(self):
        self.policy()
        request = self.prepare()
        started, release = threading.Event(), threading.Event()
        results = []
        def create(*args):
            started.set()
            if not release.wait(5):
                raise RuntimeError('test coordination timed out')
            return {'files': [], 'upstream': {'validated': True}}
        def execute():
            try:
                results.append(self.queue.execute(request))
            except Exception as error:
                results.append(error)
        with patch('spec_approval_queue.spec.create', side_effect=create) as mocked:
            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                self.assert_code('IN_PROGRESS', lambda: self.queue.execute(request))
                # Revoking cannot roll back an already-started operation.
                self.policy('disable')
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            mocked.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict)
        self.assertTrue(results[0]['created'])

    @unittest.skipUnless(shutil.which('openspec'), 'OpenSpec CLI not installed')
    def test_actual_spec_creation_and_strict_validation_in_temporary_project(self):
        # Real generator/CLI; fake console confined to this disposable fixture.
        self.policy()
        request = self.prepare()
        result = self.queue.execute(request)
        self.assertTrue(result['created'])
        self.assertTrue(result['upstream']['validated'])
        self.assertTrue(result['upstream']['sdd']['validated'])
        self.assertTrue((self.root / 'example-project/openspec/changes/safe-feature').is_dir())
        self.assertTrue(self.queue.execute(request)['replayed'])


class ConsoleTests(unittest.TestCase):
    def test_status_uses_existing_queue_and_no_terminal_prompt(self):
        import owner_project_approval as cli
        out = io.StringIO()
        with patch.object(cli, 'ApprovalQueue') as factory, patch('sys.stdout', out):
            factory.return_value.project_policy_status.return_value = {'enabled': False}
            self.assertEqual(cli.main(['status', 'example-project']), 0)
            factory.return_value.project_policy_console.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())['data']['enabled'])

    def test_enable_permanent_forwards_to_canonical_queue(self):
        import owner_project_approval as cli
        with patch.object(cli, 'ApprovalQueue') as factory, patch('sys.stdout', io.StringIO()):
            factory.return_value.project_policy_console.return_value = {'enabled': True}
            self.assertEqual(cli.main(['enable', 'example-project', '--permanent']), 0)
            factory.return_value.project_policy_console.assert_called_once_with(
                'example-project', 'enable', hours=None, permanent=True)

    def test_storage_error_redacted(self):
        import owner_project_approval as cli
        out = io.StringIO()
        with patch.object(cli, 'ApprovalQueue', side_effect=sqlite3.OperationalError('SECRET')), patch('sys.stdout', out):
            self.assertEqual(cli.main(['status', 'example-project']), 2)
        self.assertNotIn('SECRET', out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['error']['code'], 'POLICY_UNAVAILABLE')

    def test_noninteractive_cli_never_enables(self):
        import owner_project_approval as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'example-project').mkdir()
            queue = ApprovalQueue(root / 'state.sqlite3', root)
            with patch.object(cli, 'ApprovalQueue', return_value=queue), patch('sys.stdin', io.StringIO()), patch('sys.stdout', io.StringIO()):
                self.assertEqual(cli.main(['enable', 'example-project']), 2)
            self.assertFalse(queue.project_policy_status('example-project')['configured'])


if __name__ == '__main__':
    unittest.main()
