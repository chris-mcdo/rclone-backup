#!/bin/sh
set -eu

# first arg is `-l` or `--some-option`
if [ "$#" -eq 0 ]; then set -- -l 5; fi
if [ "${1#-}" != "$1" ] ; then
	set -- crond "-f" "$@"
fi

# touch log file with correct permissions if it doesn't exist
if [ -n "${RCLONE_LOG_FILE:-}" ] && [ ! -f "$RCLONE_LOG_FILE" ]; then
  touch "$RCLONE_LOG_FILE"
  chown rclone:rclone "$RCLONE_LOG_FILE"
  chmod 644 "$RCLONE_LOG_FILE"
fi

exec "$@"
