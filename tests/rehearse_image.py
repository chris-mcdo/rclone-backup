"""Disposable real-PostgreSQL/WebDAV rehearsal; no production data or secrets.

Select the image with BACKUP_REHEARSAL_IMAGE. Run where Docker is available. Removes only containers/network it created and its
synthetic temporary directory. Prints summary assertions, never raw tool output.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

IMAGE = os.environ.get('BACKUP_REHEARSAL_IMAGE', 'rclone-backup:review')
PG_IMAGE = 'postgres@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3'
prefix = 'ob-backup-rehearsal-' + uuid.uuid4().hex[:10]
containers = []
network_created = False
results = []


def call(*args, input=None, check=True, timeout=60):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Rehearsal command failed: ' + args[0] + ' ' + args[1] +
                           '; output withheld; return code ' + str(result.returncode))
    return result


def docker(*args, **kw):
    return call('docker', *args, **kw)


def start(name, *args):
    docker('run', '-d', '--name', name, '--label', 'obcollection.rehearsal=' + prefix,
           '--network', prefix, '--memory', '512m', '--cpus', '1', *args)
    containers.append(name)


def probe(job, script, env=None, check=True):
    args = ['exec']
    for key, value in (env or {}).items():
        args.extend(['-e', key + '=' + value])
    return docker(*args, job, '/bin/sh', script, check=check)


with tempfile.TemporaryDirectory(prefix=prefix + '-') as tmp:
    root = Path(tmp)
    # Only synthetic inputs: allow the image's unprivileged cron user to read them.
    os.chmod(root, 0o755)
    secret = root / 'password'
    secret.write_text(secrets.token_urlsafe(32))
    secret.chmod(0o644)
    (root / 'badpassword').write_text('synthetic-incorrect-password')
    (root / 'rclone.conf').write_text('[test]\ntype = webdav\nurl = http://storage:8080\nvendor = other\n')
    (root / 'badpassword').chmod(0o644)
    (root / 'rclone.conf').chmod(0o644)
    wrapper = root / 'rclone'
    wrapper.write_text('''#!/bin/sh
set -eu
case "${REHEARSAL_FAULT:-}:$1" in
  partial:copyto)
    case "$2" in test:*) ;; *)
      head -c 100 "$2" | /usr/local/bin/rclone rcat "$3"
      exit 22;; esac;;
  corrupt:copyto)
    case "$2" in test:*) printf corrupt | /usr/local/bin/rclone rcat "$2";; esac;;
  listing:lsf) exit 23;;
  prune:deletefile) exit 24;;
esac
exec /usr/local/bin/rclone "$@"
''')
    wrapper.chmod(0o755)
    try:
        docker('network', 'create', '--internal', '--label', 'obcollection.rehearsal=' + prefix, prefix)
        network_created = True
        pg, storage, job = [prefix + '-' + s for s in ['pg', 'storage', 'job']]
        start(pg, '--network-alias', 'database', '--tmpfs', '/var/lib/postgresql/data:rw,size=268435456',
              '--mount', 'type=bind,src=' + str(root) + ',dst=/inputs,readonly',
              '-e', 'POSTGRES_USER=rehearsal', '-e', 'POSTGRES_DB=rehearsal',
              '-e', 'POSTGRES_PASSWORD_FILE=/inputs/password', PG_IMAGE)
        for _ in range(40):
            # The image starts a temporary Unix-socket server during init; wait
            # for the final TCP listener so the seed database exists.
            if docker('exec', pg, 'pg_isready', '-h', '127.0.0.1', '-U', 'rehearsal', check=False).returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Isolated PostgreSQL did not become ready')
        docker('exec', '-i', pg, 'psql', '-U', 'rehearsal', '-d', 'rehearsal', '-v', 'ON_ERROR_STOP=1',
               input="CREATE TABLE repair_probe(id integer PRIMARY KEY, value text);\n"
                     "INSERT INTO repair_probe VALUES (1,'alpha'),(2,'beta'),(3,'gamma');\n")
        start(storage, '--network-alias', 'storage', '--tmpfs', '/remote:rw,size=268435456',
              '--entrypoint', '/usr/local/bin/rclone', IMAGE, 'serve', 'webdav', '/remote', '--addr', ':8080')
        start(job, '--user', 'rclone', '--tmpfs', '/data:rw,size=268435456,uid=1009,gid=1009',
              '--mount', 'type=bind,src=' + str(root) + ',dst=/inputs,readonly',
              '-e', 'POSTGRES_HOST=database', '-e', 'POSTGRES_DB=rehearsal',
              '-e', 'POSTGRES_USER=rehearsal', '-e', 'POSTGRES_PASSWORD_FILE=/inputs/password',
              '-e', 'RCLONE_CONFIG=/inputs/rclone.conf', '-e', 'REMOTE_PATH=test:backups',
              '-e', 'RCLONE_RETRIES=1', '-e', 'RCLONE_LOW_LEVEL_RETRIES=1',
              '-e', 'RCLONE_TIMEOUT=5s', '-e', 'RCLONE_CONNECT_TIMEOUT=2s',
              '-e', 'BACKUP_COUNT=11', '--entrypoint', '/bin/sh', IMAGE, '-c', 'exec tail -f /dev/null')
        for _ in range(30):
            if docker('exec', job, '/usr/local/bin/rclone', 'lsf', 'test:', check=False).returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Isolated WebDAV did not become ready')

        success = probe(job, '/usr/local/bin/backup.sh', {'BACKUP_FILE_PREFIX': 'success'})
        assert 'Upload verified by full read-back comparison' in success.stdout
        results.append('real PostgreSQL dump, WebDAV upload/read-back and byte comparison passed')
        # Restore the archive fetched from WebDAV, not the original local dump.
        docker('exec', job, 'mkdir', '-p', '/data/restore')
        today = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
        docker('exec', job, '/usr/local/bin/rclone', 'copyto',
               'test:backups/success-' + today + '.tar.gz', '/data/restored.tar.gz')
        docker('exec', job, 'tar', '-xzf', '/data/restored.tar.gz', '-C', '/data/restore')
        docker('exec', pg, 'createdb', '-U', 'rehearsal', 'restored')
        dump = docker('exec', job, 'find', '/data/restore', '-name', '*.db.gz', '-type', 'f').stdout.strip()
        assert len(dump.splitlines()) == 1
        # Stream binary dump through Python pipes; never decode it as text or log it.
        data = subprocess.check_output(['docker', 'exec', job, 'cat', dump], timeout=30)
        restore = subprocess.run(['docker', 'exec', '-i', pg, 'pg_restore', '-U', 'rehearsal',
                                  '-d', 'restored', '--exit-on-error'], input=data,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        assert restore.returncode == 0, 'Synthetic restore failed'
        restored = docker('exec', pg, 'psql', '-U', 'rehearsal', '-d', 'restored', '-Atc',
                          "SELECT string_agg(value, ',' ORDER BY id) FROM repair_probe").stdout.strip()
        assert restored == 'alpha,beta,gamma'
        results.append('remote archive restored into separate database; three sentinel rows matched')
        before = docker('exec', job, 'sha256sum', '/data/backup/postgresql/backup-' + today + '.db.gz').stdout
        for env, name in [({'POSTGRES_PASSWORD_FILE': '/inputs/badpassword'}, 'bad-credentials'),
                          ({'POSTGRES_DB': 'does_not_exist'}, 'missing-database')]:
            outcome = probe(job, '/usr/local/bin/backup.sh', dict(env, BACKUP_FILE_PREFIX=name), check=False)
            assert outcome.returncode != 0 and 'Backup complete:' not in outcome.stdout, name + ' did not fail as expected'
            assert docker('exec', job, 'sha256sum', '/data/backup/postgresql/backup-' + today + '.db.gz').stdout == before
            results.append(name + ' failed safely and preserved previous dump')
        now = int(time.time())
        i = 1
        while i < 11 and (now // 86400) & (1 << (i - 1)):
            i += 1
        expired_date = datetime.datetime.fromtimestamp(now - 86400 * (1 << i), datetime.timezone.utc).strftime('%Y-%m-%d')
        for fault in ['partial', 'corrupt', 'listing', 'prune']:
            old = fault + '-' + expired_date + '.tar.gz'
            # Seed through WebDAV so its VFS directory cache sees the object.
            docker('exec', '-i', job, '/usr/local/bin/rclone', 'rcat', 'test:backups/' + old, input='old')
            outcome = probe(job, '/usr/local/bin/backup.sh', {
                'BACKUP_FILE_PREFIX': fault, 'REHEARSAL_FAULT': fault,
                'PATH': '/inputs:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}, check=False)
            assert outcome.returncode != 0 and 'Backup complete:' not in outcome.stdout, fault + ' did not fail as expected; synthetic output: ' + outcome.stdout + outcome.stderr
            assert docker('exec', job, 'test', '-s', '/data/upload/' + fault + '-' + today + '.tar.gz', check=False).returncode == 0
            assert docker('exec', storage, 'test', '-s', '/remote/backups/' + old, check=False).returncode == 0
            results.append(fault + ' failure retained local archive and old remote backup')
        print(json.dumps({'candidate': IMAGE, 'postgres_image': PG_IMAGE,
                          'network': 'internal; no published ports; synthetic credentials only',
                          'backup_user': 'rclone (same account as production cron)',
                          'results': results, 'passed': True}, indent=2), flush=True)
    finally:
        for name in reversed(containers):
            docker('rm', '-f', name, check=False)
        if network_created:
            docker('network', 'rm', prefix, check=False)
