"""Failure-injection tests using temporary files and synthetic tool replacements.

No Docker, production data, credentials, network or real database is used.
Run: python3 -m unittest discover -s tests -p 'test_*.py' -v
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
STUB = r'''#!/usr/bin/python3
import json, os, pathlib, shutil, stat, sys
tool=pathlib.Path(sys.argv[0]).name
a=sys.argv[1:]
root=pathlib.Path(os.environ['TEST_ROOT'])
failure=os.environ.get('FAIL_AT','')
with (root/'calls').open('a') as f: f.write(tool+' '+(' '.join(a) if tool=='rclone' else '')+'\n')
if tool=='run-parts':
    sys.exit(7 if failure=='dump_hook' else 0)
if tool=='tar':
    sys.exit(9)
if tool=='pg_dump':
    pgpass=pathlib.Path(os.environ['PGPASSFILE'])
    (root/'pgpass-observed.json').write_text(json.dumps({'mode':stat.S_IMODE(pgpass.stat().st_mode),'content':pgpass.read_text()}))
    print('synthetic dump bytes')
    sys.exit(17 if failure=='pg_dump' else 0)
if tool=='pg_restore':
    sys.exit(18 if failure=='catalog' else 0)
if tool=='rclone':
    operation=a[0]
    if operation=='copyto':
        upload=not a[1].startswith('fake:')
        if failure==('upload' if upload else 'download'):sys.exit(19)
        source=pathlib.Path(a[1]) if upload else root/'remote'/a[1].split('/')[-1]
        target=root/'remote'/a[2].split('/')[-1] if upload else pathlib.Path(a[2])
        shutil.copyfile(source,target)
        if not upload and failure=='mismatch':target.write_bytes(b'corrupt')
    elif operation=='lsf':
        if failure=='listing':sys.exit(20)
        print('\n'.join(p.name for p in (root/'remote').iterdir()))
    elif operation=='deletefile':
        if failure=='prune':sys.exit(21)
        (root/'remote'/a[1].split('/')[-1]).unlink()
    else:sys.exit('Unexpected rclone operation')
'''


class BackupRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ['bin', 'remote', 'data', 'upload', 'dumps', 'tmp']:
            (self.root / name).mkdir()
        (self.root / 'data' / 'sample').write_text('synthetic backup data')
        for tool in ['run-parts', 'rclone', 'pg_dump', 'pg_restore']:
            self.stub(tool)
        self.env = dict(os.environ, TEST_ROOT=str(self.root),
                        PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                        BACKUP_FOLDER=str(self.root / 'data'),
                        OUTPUT_DIR=str(self.root / 'upload'),
                        DUMP_DIRECTORY=str(self.root / 'dumps'),
                        TMPDIR=str(self.root / 'tmp'),
                        REMOTE_PATH='fake:backups', BACKUP_COUNT='11',
                        BACKUP_FILE_PREFIX='sample', DUMP_FILE_PREFIX='backup',
                        POSTGRES_HOST='synthetic-host', POSTGRES_PORT='5432',
                        POSTGRES_USER='synthetic-user', POSTGRES_DB='synthetic-db',
                        POSTGRES_PASSWORD='synthetic-password')
        # Prevent inherited settings from altering the synthetic test setup.
        for key in list(self.env):
            if key.endswith('_FILE') and key.startswith('POSTGRES_'):
                del self.env[key]
        now = int(subprocess.check_output(['date', '-u', '+%s']))
        i = 1
        while i < 11 and (now // 86400) & (1 << (i - 1)):
            i += 1
        expired = subprocess.check_output(['date', '-u', '-d', '@' + str(now - 86400 * (1 << i)), '+%F'], text=True).strip()
        self.expired = self.root / 'remote' / ('sample-' + expired + '.tar.gz')
        self.expired.write_bytes(b'previous backup')

    def stub(self, tool):
        p = self.root / 'bin' / tool
        p.write_text(STUB)
        p.chmod(0o700)

    def run_script(self, name='backup.sh', failure=''):
        return subprocess.run(['/bin/sh', str(SCRIPTS / ('dump-scripts/dump-postgres.sh' if name == 'dump-postgres.sh' else name))], env=dict(self.env, FAIL_AT=failure),
                              text=True, capture_output=True, timeout=15)

    def calls(self):
        p = self.root / 'calls'
        return p.read_text() if p.exists() else ''

    def assert_not_pruned(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.expired.exists())
        self.assertNotIn('deletefile', self.calls())
        self.assertNotIn('Backup complete:', result.stdout)
        self.assertFalse((self.root / 'upload' / '.backup.lock').exists())

    def test_success_verifies_before_pruning_and_cleans_local(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertLess(calls.index('copyto fake:'), calls.index('deletefile'))
        self.assertFalse(self.expired.exists())
        self.assertEqual(list((self.root / 'upload').iterdir()), [])
        self.assertEqual(len(list((self.root / 'remote').iterdir())), 1)

    def test_dump_hook_failure_stops_before_upload(self):
        result = self.run_script(failure='dump_hook')
        self.assert_not_pruned(result)
        self.assertNotIn('rclone', self.calls())

    def test_archive_failure_stops_before_upload(self):
        self.stub('tar')
        result = self.run_script()
        self.assert_not_pruned(result)
        self.assertNotIn('rclone', self.calls())
        self.assertEqual(list((self.root / 'upload').iterdir()), [])

    def test_upload_readback_and_listing_failures_retain_archive(self):
        for failure in ['upload', 'download', 'mismatch', 'listing']:
            with self.subTest(failure=failure):
                # Independent state for each injected failure.
                for p in (self.root / 'upload').iterdir():
                    p.unlink()
                (self.root / 'calls').write_text('')
                result = self.run_script(failure=failure)
                self.assert_not_pruned(result)
                self.assertEqual(len(list((self.root / 'upload').glob('*.tar.gz'))), 1)
                self.assertFalse(list((self.root / 'upload').glob('.*')))

    def test_pruning_failure_is_not_reported_as_success(self):
        result = self.run_script(failure='prune')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Backup complete:', result.stdout)
        self.assertTrue(self.expired.exists())
        self.assertEqual(len(list((self.root / 'upload').glob('*.tar.gz'))), 1)

    def test_missing_expired_backup_is_success(self):
        self.expired.unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('deletefile', self.calls())

    def test_retry_does_not_overwrite_unsent_archive(self):
        self.run_script(failure='upload')
        archive = next((self.root / 'upload').glob('*.tar.gz'))
        archive.write_bytes(b'retained failed upload')
        (self.root / 'calls').write_text('')
        result = self.run_script()
        self.assert_not_pruned(result)
        self.assertEqual(archive.read_bytes(), b'retained failed upload')
        self.assertEqual(self.calls(), '')

    def test_existing_lock_prevents_concurrent_backup(self):
        (self.root / 'upload' / '.backup.lock').mkdir()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), '')
        self.assertTrue((self.root / 'upload' / '.backup.lock').is_dir())

    def test_invalid_retention_and_prefix_rejected(self):
        for key, value in [('BACKUP_COUNT', '0'), ('BACKUP_COUNT', '999'),
                           ('BACKUP_COUNT', 'bad'), ('BACKUP_FILE_PREFIX', '../bad')]:
            with self.subTest(key=key, value=value):
                old = self.env[key]
                self.env[key] = value
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertEqual(self.calls(), '')
                self.env[key] = old

    def test_dump_failure_preserves_previous_and_removes_temp_credentials(self):
        old = self.root / 'dumps' / 'backup-2026-01-01.db.gz'
        old.write_bytes(b'previous known dump')
        for failure in ['pg_dump', 'catalog']:
            result = self.run_script('dump-postgres.sh', failure)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(old.read_bytes(), b'previous known dump')
            self.assertEqual(list((self.root / 'tmp').iterdir()), [])
            self.assertEqual(list((self.root / 'dumps').iterdir()), [old])

    def test_successful_dump_is_private_atomic_and_keeps_previous_generation(self):
        for day in ['01', '02', '03']:
            (self.root / 'dumps' / ('backup-2026-01-' + day + '.db.gz')).write_bytes(b'old dump')
        result = self.run_script('dump-postgres.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        files = sorted((self.root / 'dumps').iterdir())
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].name, 'backup-2026-01-03.db.gz')
        self.assertEqual(files[1].stat().st_mode & 0o777, 0o600)
        self.assertEqual(list((self.root / 'tmp').iterdir()), [])

    def test_secret_file_values_are_escaped_and_never_evaluated(self):
        password = 'synthetic:back\\slash$(touch SHOULD_NOT_EXIST)'
        secret = self.root / 'synthetic-secret'
        secret.write_text(password)
        del self.env['POSTGRES_PASSWORD']
        self.env['POSTGRES_PASSWORD_FILE'] = str(secret)
        result = self.run_script('dump-postgres.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads((self.root / 'pgpass-observed.json').read_text())
        self.assertEqual(observed['mode'], 0o600)
        self.assertIn('synthetic\\:back\\\\slash$(touch SHOULD_NOT_EXIST)', observed['content'])
        self.assertNotIn(password, result.stdout + result.stderr)
        self.assertFalse(Path('SHOULD_NOT_EXIST').exists())

    def test_missing_or_conflicting_secret_files_stop_before_dump(self):
        self.env['POSTGRES_PASSWORD_FILE'] = str(self.root / 'missing')
        self.assertNotEqual(self.run_script('dump-postgres.sh').returncode, 0)
        del self.env['POSTGRES_PASSWORD']
        self.assertNotEqual(self.run_script('dump-postgres.sh').returncode, 0)
        self.assertNotIn('pg_dump', self.calls())


if __name__ == '__main__':
    unittest.main()
