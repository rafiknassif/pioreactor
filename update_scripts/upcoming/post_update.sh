#!/bin/bash

set -x
set -e

export LC_ALL=C


# since we are changing the db, we should restart this
sudo systemctl restart pioreactor_startup_run@mqtt_to_db_streaming.service