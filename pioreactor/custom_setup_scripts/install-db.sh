#!/bin/bash

set -x
set -e

export LC_ALL=C

USERNAME=pioreactor
STORAGE_DIR=/home/$USERNAME/.pioreactor/storage

DB=$STORAGE_DIR/pioreactor.sqlite

touch $DB
touch $DB-shm
touch $DB-wal

chmod -R 770 $STORAGE_DIR
chown -R $USERNAME:www-data $STORAGE_DIR
chmod g+s $STORAGE_DIR

sqlite3 $DB < sql/sqlite_configuration.sql
sqlite3 $DB < sql/create_tables.sql
sqlite3 $DB < sql/create_triggers.sql


