#!/bin/sh
# Fail closed: upload, read back and compare before pruning or local removal.
set -eu
umask 077

BACKUP_FOLDER=${BACKUP_FOLDER:-/data/backup}
REMOTE_PATH=${REMOTE_PATH:-backup:backup}
BACKUP_COUNT=${BACKUP_COUNT:-10}
BACKUP_FILE_PREFIX=${BACKUP_FILE_PREFIX:-backup}
DUMP_SCRIPT_DIRECTORY=${DUMP_SCRIPT_DIRECTORY:-/usr/local/bin/backup-dump.d}
OUTPUT_DIR=${OUTPUT_DIR:-/data/upload}
export RCLONE_USE_JSON_LOG=${RCLONE_USE_JSON_LOG:-true}
export RCLONE_LOG_LEVEL=${RCLONE_LOG_LEVEL:-INFO}

case "$BACKUP_COUNT" in ''|*[!0-9]*) echo 'Invalid retention count' >&2; exit 1;; esac
[ "$BACKUP_COUNT" -ge 1 ] && [ "$BACKUP_COUNT" -le 20 ] || exit 1
case "$BACKUP_FILE_PREFIX" in ''|*[!A-Za-z0-9_-]*) echo 'Invalid archive prefix' >&2; exit 1;; esac
mkdir -p "$OUTPUT_DIR"
LOCK_DIR="$OUTPUT_DIR/.backup.lock"
if ! mkdir "$LOCK_DIR"; then echo 'Backup already running or stale lock requires review' >&2; exit 1; fi
PARTIAL=''
VERIFY=''
LISTING=''
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    [ -z "$PARTIAL" ] || rm -f -- "$PARTIAL"
    [ -z "$VERIFY" ] || rm -f -- "$VERIFY"
    [ -z "$LISTING" ] || rm -f -- "$LISTING"
    rmdir "$LOCK_DIR" || :
    if [ "$result" -ne 0 ]; then echo 'Backup failed; completed local archives retained; inspect logs' >&2; fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

echo 'Starting backup'
NOW=$(date -u +%s)
TODAY=$(date -u -d "@$NOW" +%F)
OUTPUT_FILE="$BACKUP_FILE_PREFIX-$TODAY.tar.gz"
LOCAL_ARCHIVE="$OUTPUT_DIR/$OUTPUT_FILE"
# Do not overwrite an archive left by a failed upload, including same-day retries.
if [ -e "$LOCAL_ARCHIVE" ]; then
    echo 'Unsent local archive exists; recover it before retrying' >&2
    exit 1
fi
run-parts --exit-on-error "$DUMP_SCRIPT_DIRECTORY"
PARTIAL=$(mktemp "$OUTPUT_DIR/.archive.XXXXXX")
tar --create --gzip --file="$PARTIAL" "$BACKUP_FOLDER"
gzip -t "$PARTIAL"
mv -- "$PARTIAL" "$LOCAL_ARCHIVE"
PARTIAL=''

rclone copyto "$LOCAL_ARCHIVE" "${REMOTE_PATH%/}/$OUTPUT_FILE"
VERIFY=$(mktemp "$OUTPUT_DIR/.verify.XXXXXX")
rclone copyto "${REMOTE_PATH%/}/$OUTPUT_FILE" "$VERIFY"
cmp -s "$LOCAL_ARCHIVE" "$VERIFY"
echo 'Upload verified by full read-back comparison'

# Preserve the existing Tower of Hanoi selection, with a single UTC day anchor.
DAY_NUMBER=$((NOW / 86400))
i=1
while [ "$i" -lt "$BACKUP_COUNT" ]; do
    OFFSET=$((1 << (i - 1)))
    [ $((DAY_NUMBER & OFFSET)) -ne 0 ] || break
    i=$((i + 1))
done
EXPIRED_DATE=$(date -u -d "@$((NOW - 86400 * (1 << i)))" +%F)
EXPIRED_FILE="$BACKUP_FILE_PREFIX-$EXPIRED_DATE.tar.gz"
LISTING=$(mktemp "$OUTPUT_DIR/.listing.XXXXXX")
# Listing failure is an error, distinct from a missing expired object.
rclone lsf "$REMOTE_PATH" --max-depth 1 --files-only > "$LISTING"
if grep -Fxq "$EXPIRED_FILE" "$LISTING"; then
    rclone deletefile "${REMOTE_PATH%/}/$EXPIRED_FILE"
fi
rm -- "$LOCAL_ARCHIVE"
echo 'Backup complete: upload verified and retention finished'
