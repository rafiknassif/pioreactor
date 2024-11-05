CREATE TABLE IF NOT EXISTS lightrod_temperatures (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    channel1_top_temp REAL,
    channel1_middle_temp REAL,
    channel1_bottom_temp REAL,
    channel1_timestamp TEXT,
    channel2_top_temp REAL,
    channel2_middle_temp REAL,
    channel2_bottom_temp REAL,
    channel2_timestamp TEXT,
    channel3_top_temp REAL,
    channel3_middle_temp REAL,
    channel3_bottom_temp REAL,
    channel3_timestamp TEXT,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS lightrod_temperatures_ix
ON lightrod_temperatures (experiment, pioreactor_unit, timestamp);
