#!/bin/bash
# script to reimport data from a exported Pioreactor
# bash import_data.sh archive_name.zip

set -x
set -e

export LC_ALL=C


ARCHIVE_NAME=$1

# Extract the hostname from the archive name
ARCHIVE_HOSTNAME=$(echo "$ARCHIVE_NAME" | cut -d'_' -f 2)

# Get the current hostname of the system
CURRENT_HOSTNAME=$(hostname)

# the hostname of this system and the archive file should be the same. Exit if not.
if [ "$ARCHIVE_HOSTNAME" != "$CURRENT_HOSTNAME" ]; then
  echo "Error: Hostname of the archive does not match this hostname."
  exit 1
fi


# stop everything that might touch the database or config files...
pio kill --all-jobs
pio kill monitor
pio kill mqtt_to_db_streaming
pio kill watchdog
sudo systemctl stop lighttpd.service
sudo systemctl stop huey.service

# blow away the old .pioreactor
rm -rf .pioreactor/

# create the new .pioreactor/
tar -xzf ARCHIVE_NAME

# rename the sqlite .backup
mv .pioreactor/storage/pioreactor.sqlite.backup .pioreactor/storage/pioreactor.sqlite

# confirm permissions
chmod -R 770 .pioreactor/storage/

echo "Done! Rebooting..."

sudo reboot
