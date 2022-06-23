#!/bin/sh
####################################
#
# PostgreSQL database dump script
#
# Dump a PostgreSQL database to a file
# Best run as a cron job. Creates a database dump
# and writes it to a directory
#
####################################

# Flags from environment

# DUMP_DIRECTORY: folder to write the dump to. The default is "/data/backup/postgresql"
# DUMP_FILE_PREFIX: prefix to the dump file name. Default is "backup". The resulting files are called (e.g.) backup-2022-07-02.db.gz

# POSTGRES_PASSWORD[_FILE]: (file containing) password of the postgres role used to generate the dump.
# POSTGRES_HOST[_FILE]: (file containing) name of the host where postgres can be found.
# POSTGRES_PORT[_FILE]: (file containing) port that postgres is listening on. Default is 5432
# POSTGRES_USER[_FILE]: (file containing) username of the postgres role used to generate the dump. Default is "backup"
# POSTGRES_DB[_FILE]: (file containing) name of the postgres database to dump.

# file_env
# set a variable from a file or from the environment
# does not export the variable
# usage: file_env VAR [DEFAULT]
#    e.g.: file_env 'XYZ_DB_PASSWORD' 'example'
file_env() {
    local var="$1"
    local fileVar="${var}_FILE"
    local def="${2:-}"
    if [ "$(eval echo \$$var)" ] && [ "$(eval echo \$$fileVar)" ]; then
        echo >&2 "error: both $var and $fileVar are set (but are exclusive)"
        exit 1
    fi
    local val="$def"
    if [ "$(eval echo \$$var)" ]; then
        val="$(eval echo \$$var)"
    elif [ "$(eval echo \$$fileVar)" ]; then
        val="$(cat "$(eval echo \$$fileVar)")"
    fi
    eval $var=\$val
    unset "$fileVar"
}

# Config
DUMP_DIRECTORY=${DUMP_DIRECTORY:-"/data/backup/postgresql"}
DUMP_FILE_PREFIX=${DUMP_FILE_PREFIX:-"backup"}

# set up dump directory
mkdir -p ${DUMP_DIRECTORY}
rm -rf ${DUMP_DIRECTORY}/*

# set up environment
file_env 'POSTGRES_PASSWORD'
file_env 'POSTGRES_HOST'
file_env 'POSTGRES_PORT' '5432'
file_env 'POSTGRES_USER' 'backup'
file_env 'POSTGRES_DB' "$POSTGRES_USER"

# Construct a temporary password file
echo "Creating temporary password file"
export PGPASSFILE="/tmp/rclone.pgpass"
$(umask 077; touch ${PGPASSFILE})
echo "${POSTGRES_HOST}:${POSTGRES_PORT}:${POSTGRES_DB}:${POSTGRES_USER}:${POSTGRES_PASSWORD}" \
    > ${PGPASSFILE}

# Dump file
# https://www.postgresql.org/docs/current/app-pgdump.html
OUTPUT_FILE_PATH="${DUMP_DIRECTORY}/${DUMP_FILE_PREFIX}-$(date +%F).db.gz"
pg_dump -Fc -d "$POSTGRES_DB" -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -w > "$OUTPUT_FILE_PATH"

# Remove password file and unset variable
echo "Removing temporary password file"
rm "${PGPASSFILE}"
unset "PGPASSFILE"
