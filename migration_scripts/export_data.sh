#!/bin/bash

set -x
set -e

export LC_ALL=C

# script to back up Pioreactor


EXPORT_NAME=pioreactor_$(hostname)_$(date +%Y%m%d).tar.gz

echo "Starting export of all data. Don't run anything. The Pioreactor UI will be inaccessible.  This may take a few minutes..."

# stop everything that might touch the database or config files...
pio kill --all-jobs  > /dev/null
pio kill monitor
pio kill mqtt_to_db_streaming
pio kill watchdog
sudo systemctl stop lighttpd.service
sudo systemctl stop huey.service



# back up database. Use || since older version might not have it.
pio run backup_database --force || pio run backup_database


# gzip .pioreactor/
# export so UI can download it, this only works on leader...
tar --exclude='*.sqlite' --exclude='*.sqlite-wal'  --exclude='*.sqlite-shm' --exclude=".pioreactor/plugins/__pycache__" -zcvf $EXPORT_NAME .pioreactor/


sudo systemctl start lighttpd.service
sudo systemctl start huey.service
sudo systemctl start pioreactor_startup_run@mqtt_to_db_streaming.service
sudo systemctl start pioreactor_startup_run@watchdog.service
sudo systemctl start pioreactor_startup_run@monitor.service

echo "Your export is ready as $EXPORT_NAME"
