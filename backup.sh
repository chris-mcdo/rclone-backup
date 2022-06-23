#!/bin/sh
####################################
#
# Tower of Hanoi backup script
#
# Archive and back up a directory using rclone
# If run as a daily cron job, archives are rotated on a Tower of Hanoi schedule
# Archives from 1, 2, 4, 8, 16... $BACKUP_COUNT days ago are retained
#
####################################

# Flags from environment
# BACKUP_FOLDER: folder to back up. The default is /data
# REMOTE_PATH: custom path for backup, e.g. RCLONE_PATH="backup:mybucket/path/to/dir". Default is "backup:backup"
# BACKUP_COUNT: number of backups to make. Default is 10. n backups means 2^(n-1) days of backup data
# BACKUP_FILE_PREFIX: prefix to the backup file name. Default is backup. The resulting files are called (e.g.) backup-2022-07-02.tar.gz
# RCLONE_CONFIG: specifies a config file to use. (No default.)
# RCLONE_LOG_LEVEL: specify the log level to use. The default is "NOTICE"
# RCLONE_{MY_EXTRA_ARG}: additional arguments to be passed to rclone.

# General config
BACKUP_FOLDER=${BACKUP_FOLDER:-"/data/backup"}
REMOTE_PATH=${REMOTE_PATH:-"backup:backup"}
BACKUP_COUNT=${BACKUP_COUNT:-10}
BACKUP_FILE_PREFIX=${BACKUP_FILE_PREFIX:-"backup"}

# rclone settings
export RCLONE_USE_JSON_LOG=${RCLONE_USE_JSON_LOG:-"true"}
export RCLONE_LOG_LEVEL=${RCLONE_LOG_LEVEL:-"INFO"}

# data dump config
DUMP_SCRIPT_DIRECTORY="/usr/local/bin/backup-dump.d"

# output path
OUTPUT_DIR="/data/upload"
OUTPUT_FILE="$BACKUP_FILE_PREFIX-$(date +%F).tar.gz"
LOCAL_ARCHIVE="${OUTPUT_DIR}/${OUTPUT_FILE}"

####################################

echo "Starting backup script"
echo $(date)
echo

echo "Running dump scripts in $DUMP_SCRIPT_DIRECTORY"
run-parts --exit-on-error ${DUMP_SCRIPT_DIRECTORY}
echo "Dump scripts completed successfully"
echo

echo "Backing up folder $BACKUP_FOLDER to remote $REMOTE_PATH"
echo

echo "Archiving and compressing backup"
tar --create --gzip --file=${LOCAL_ARCHIVE} ${BACKUP_FOLDER}
echo "Archive completed"
echo

# Use custom config if specified
echo "Uploading backup via rclone"
rclone copy ${LOCAL_ARCHIVE} ${REMOTE_PATH}
echo "Upload complete"
echo

# Delete local copy
rm ${LOCAL_ARCHIVE}

# Compute which "tape" to overwrite (i.e. which file to delete)
# (requires some math to work this out)
DAY_NUMBER=$(($(date +%s) / 86400))
for i in $(seq 1 $BACKUP_COUNT)
do
    OFFSET=$(( 1 << (i - 1) )) # "offset" for tape i is 2^(i-1)
    if [ $(($DAY_NUMBER & $OFFSET)) -eq "0" ] # yes, bitwise "and"
    then
        break
    fi
done
EXPIRED_FILE_DATE=$(date -d "@$(($(date +%s) - (86400 * (1 << i))))" +%F)

# Subtract the required number of days (2^i) manually
# Warning: probably doesn't play nice with timezones
# Deletes oldest backup when there aren't enough "tapes"

# Remove expired backups
EXPIRED_FILE_PATH="${REMOTE_PATH}/${BACKUP_FILE_PREFIX}-${EXPIRED_FILE_DATE}.tar.gz"

echo "Attempting to remove expired backup: ${EXPIRED_FILE_PATH}"
EXPIRED_FILE_EXISTS=$(rclone lsf ${EXPIRED_FILE_PATH})
if [ -n "${EXPIRED_FILE_EXISTS}" ]
then
    rclone delete ${EXPIRED_FILE_PATH}
else
    echo "Expired backup ${EXPIRED_FILE_PATH} not found. Skipping deletion."
fi

# Finished
echo "Backup complete"
