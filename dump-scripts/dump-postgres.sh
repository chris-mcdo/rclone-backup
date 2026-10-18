#!/bin/sh
# Atomic custom-format dump; never discard the previous dump on failure.
set -eu
umask 077
DUMP_DIRECTORY=${DUMP_DIRECTORY:-/data/backup/postgresql}
DUMP_FILE_PREFIX=${DUMP_FILE_PREFIX:-backup}
case "$DUMP_FILE_PREFIX" in ''|*[!A-Za-z0-9_-]*) echo 'Invalid dump prefix' >&2; exit 1;; esac

read_setting() {
    # Names are fixed at the call sites. Values are never evaluated as shell code.
    name=$1
    fallback=$2
    eval 'direct=${'"$name"'-}'
    eval 'file=${'"$name"'_FILE-}'
    if [ -n "$direct" ] && [ -n "$file" ]; then echo 'Conflicting database settings' >&2; return 1; fi
    if [ -n "$file" ]; then cat -- "$file"; else printf '%s' "${direct:-$fallback}"; fi
}
POSTGRES_PASSWORD=$(read_setting POSTGRES_PASSWORD '')
POSTGRES_HOST=$(read_setting POSTGRES_HOST '')
POSTGRES_PORT=$(read_setting POSTGRES_PORT 5432)
POSTGRES_USER=$(read_setting POSTGRES_USER backup)
POSTGRES_DB=$(read_setting POSTGRES_DB "$POSTGRES_USER")
: "${POSTGRES_PASSWORD:?Missing database password}" "${POSTGRES_HOST:?Missing database host}"
# pgpass is one record; reject line breaks rather than creating unintended records.
NL='
'
for value in "$POSTGRES_PASSWORD" "$POSTGRES_HOST" "$POSTGRES_PORT" "$POSTGRES_USER" "$POSTGRES_DB"; do
    case "$value" in *"$NL"*) echo 'Newlines are unsupported in database settings' >&2; exit 1;; esac
done
PGPASSFILE=''
PARTIAL=''
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    [ -z "$PGPASSFILE" ] || rm -f -- "$PGPASSFILE"
    [ -z "$PARTIAL" ] || rm -f -- "$PARTIAL"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
PGPASSFILE=$(mktemp "${TMPDIR:-/tmp}/backup.pgpass.XXXXXX")
export PGPASSFILE
escape_pgpass() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/:/\\:/g'; }
printf '%s:%s:%s:%s:%s\n' "$(escape_pgpass "$POSTGRES_HOST")" \
    "$(escape_pgpass "$POSTGRES_PORT")" "$(escape_pgpass "$POSTGRES_DB")" \
    "$(escape_pgpass "$POSTGRES_USER")" "$(escape_pgpass "$POSTGRES_PASSWORD")" > "$PGPASSFILE"
unset POSTGRES_PASSWORD PGPASSWORD
mkdir -p "$DUMP_DIRECTORY"
OUTPUT="$DUMP_DIRECTORY/$DUMP_FILE_PREFIX-$(date -u +%F).db.gz"
PARTIAL=$(mktemp "$DUMP_DIRECTORY/.dump.XXXXXX")
pg_dump -Fc -d "$POSTGRES_DB" -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -w > "$PARTIAL"
[ -s "$PARTIAL" ]
pg_restore --list "$PARTIAL" > /dev/null
mv -- "$PARTIAL" "$OUTPUT"
PARTIAL=''

# Keep today's dump and the newest earlier generation; remove only owned dated files.
PREVIOUS=''
for file in "$DUMP_DIRECTORY/$DUMP_FILE_PREFIX-"????-??-??.db.gz; do
    [ -f "$file" ] && [ "$file" != "$OUTPUT" ] || continue
    if [ -z "$PREVIOUS" ] || [ "$file" \> "$PREVIOUS" ]; then PREVIOUS=$file; fi
done
for file in "$DUMP_DIRECTORY/$DUMP_FILE_PREFIX-"????-??-??.db.gz; do
    [ -f "$file" ] || continue
    [ "$file" = "$OUTPUT" ] || [ "$file" = "$PREVIOUS" ] || rm -- "$file"
done
echo 'Database dump catalog verified'
