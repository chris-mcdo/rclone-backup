FROM rclone/rclone:1

# Install postgresql client library
RUN apk add --no-cache postgresql-client

# Remove crontab junk
RUN set -eux; \
    rm -rf /etc/crontabs/* /etc/periodic

# Make backup and data directories
RUN set -eux; \
    mkdir -p /data/backup /data/upload; \
    chown -R rclone:rclone /data/backup /data/upload

# Entrypoint and backup script
COPY --chmod=0775 docker-entrypoint.sh backup.sh /usr/local/bin/

# Default dump scripts
COPY --chmod=0775 dump-scripts/* /usr/local/bin/backup-dump.d/

# Custom crontabs
COPY --chmod=0600 crontabs/* /etc/crontabs/

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["-l", "5"]
