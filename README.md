# rclone-backup

A docker image to automate backups, using rclone.

Comes with a script to back up a PostgreSQL database.

## Overview

A simple backup script looks like this:

1) Dump some data (e.g. a database backup) to a directory on the local  machine.

2) Compress this local directory and upload it to cloud storage.

3) Delete old / expired backups from cloud storage when they are no longer needed.

If you run this script periodically, you have a simple backup scheme.

`rclone-backup` uses this scheme. It is implemented as follows:

* A backup script (`/usr/local/bin/backup.sh`) is run as a daily `cron` job.

* This backup script:

    * Runs any scripts found in the `/usr/local/bin/backup-dump.d` directory (e.g. scripts which dump data to the backup directory).

    * Archives and compresses the backup directory (default `/data/backup`) and places it in `/data/upload`.

    * Uploads the file in `/data/upload` to cloud storage using [rclone](https://rclone.org/).

    * Uses a [Tower of Hanoi](https://en.wikipedia.org/wiki/Backup_rotation_scheme) backup rotation scheme to decide which backups to keep / delete on the cloud storage.

## Usage

The `rclone-backup` container can be configured using environment variables:

* Configuration for `backup.sh` (see the file for details): `BACKUP_FOLDER`,
`REMOTE_PATH`, `BACKUP_COUNT`, `BACKUP_FILE_PREFIX`,  `RCLONE_CONFIG`,
`RCLONE_LOG_LEVEL`. Most have sensible defaults set.

* Configuration for `dump-postgres.sh` (see file for details): `DUMP_DIRECTORY`,
`DUMP_FILE_PREFIX`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_USER`, `POSTGRES_DB`.

Alternatively, you can customize the image by:

* Adding "dump" scripts to the `/usr/local/bin/backup-dump.d/` directory (see `scripts` in this repository for an example).

* Modifying the `/usr/local/bin/backup.sh` script.

* Modifying the crontabs in `/etc/crontabs/` to use a different user and/or execute backups on a different schedule.

If the default crontab is used, the `rclone` user executes all scripts, so this user must have permission to execute scripts and create data dumps.

## Logs

The `stdout`/`stderr` of all scripts are written to the `stdout`/`stderr` of the container by default.

## License

See the
[License](https://github.com/chris-mcdo/rclone-backup/blob/main/LICENSE)
file for details.

## Failure handling and verification

Dump hooks and archive/upload/read-back failures stop the job before pruning.
Every uploaded archive is downloaded completely and byte-compared before exact-file
retention deletion. Failed uploads retain completed local archives; a same-day
retry refuses to overwrite them. Inspect and recover retained archives before
retrying. An interrupted job can leave a lock requiring manual review.

PostgreSQL dumps are written atomically and catalog-checked. The two newest dated
dumps are retained locally, increasing archive size and scratch-space needs.
The historical `.db.gz` suffix denotes PostgreSQL custom format, without an outer
gzip wrapper. A catalog check is not a full restore test. Credentials may use
the existing environment variables or their `_FILE` counterparts; do not set both.

Allow space for the current archive, its complete verification download, retained
dumps and temporary files. Backup scope and scheduling are unchanged.

Run synthetic failure-injection tests with:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The image's base/rclone/PostgreSQL client upgrade is a separate change. Review and
pin supported versions before building the final catch-up upgrade image.
