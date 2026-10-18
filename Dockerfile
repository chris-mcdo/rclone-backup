FROM rclone/rclone:1.75.2@sha256:2687085f718d3c628f7fdfb77c52a1d344332aed543110bb88088f2c70d43eb5

# Install postgresql client library
RUN apk add --no-cache postgresql17-client coreutils tar gzip diffutils

# Remove crontab junk
RUN set -eux; \
    rm -rf /etc/crontabs/* /etc/periodic

# Make backup and data directories
RUN set -eux; \
    mkdir -p /data/backup /data/upload; \
    chown -R rclone:rclone /data/backup /data/upload

# Entrypoint and backup script
COPY docker-entrypoint.sh backup.sh /usr/local/bin/

# Default dump scripts
COPY dump-scripts/* /usr/local/bin/backup-dump.d/

# Custom crontabs
COPY crontabs/* /etc/crontabs/
RUN chmod 0775 /usr/local/bin/docker-entrypoint.sh /usr/local/bin/backup.sh \
       /usr/local/bin/backup-dump.d/* && chmod 0600 /etc/crontabs/* \
    && pg_dump --version && pg_restore --version && rclone version \
    && apk info -v > /usr/local/share/backup-packages.txt

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["-l", "5"]
