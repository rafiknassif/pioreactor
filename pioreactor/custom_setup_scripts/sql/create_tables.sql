--DROP TABLE IF EXISTS lightrod_temperatures;

CREATE TABLE IF NOT EXISTS lightrod_temperatures (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    LR_A_top_temp REAL,
    LR_A_middle_temp REAL,
    LR_A_bottom_temp REAL,
    LR_A_timestamp TEXT,
    LR_B_top_temp REAL,
    LR_B_middle_temp REAL,
    LR_B_bottom_temp REAL,
    LR_B_timestamp TEXT,
    LR_C_top_temp REAL,
    LR_C_middle_temp REAL,
    LR_C_bottom_temp REAL,
    LR_C_timestamp TEXT,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS lightrod_temperatures_ix
ON lightrod_temperatures (experiment, pioreactor_unit, timestamp);
