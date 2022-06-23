#!/bin/sh
set -e

# first arg is `-l` or `--some-option`
if [ "${1#-}" != "$1" ] ; then
	set -- crond "-f" "$@"
fi

# touch log file with correct permissions if it doesn't exist
if [ "${RCLONE_LOG_FILE}" -a ! -f "${RCLONE_LOG_FILE}" ]; then
  touch ${RCLONE_LOG_FILE}
  chown rclone:rclone ${RCLONE_LOG_FILE}
  chmod 644 ${RCLONE_LOG_FILE}
fi

exec "$@"
