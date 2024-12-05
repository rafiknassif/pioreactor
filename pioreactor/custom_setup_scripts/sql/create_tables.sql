--DROP TABLE IF EXISTS lightrod_temperatures;

--CREATE TABLE IF NOT EXISTS lightrod_temperatures (
--    experiment TEXT NOT NULL,
--    pioreactor_unit TEXT NOT NULL,
--    timestamp TEXT NOT NULL,
--    LR_A_top_temp REAL,
--    LR_A_middle_temp REAL,
--    LR_A_bottom_temp REAL,
--    LR_A_timestamp TEXT,
--    LR_B_top_temp REAL,
--    LR_B_middle_temp REAL,
--    LR_B_bottom_temp REAL,
--    LR_B_timestamp TEXT,
--    LR_C_top_temp REAL,
--    LR_C_middle_temp REAL,
--    LR_C_bottom_temp REAL,
--    LR_C_timestamp TEXT,
--    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
--);
--
CREATE INDEX IF NOT EXISTS lightrod_temperatures_ix
ON lightrod_temperatures (experiment, pioreactor_unit, timestamp);

DROP TABLE IF EXISTS pbr_temperature;

CREATE TABLE IF NOT EXISTS pbr_temperature (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    pbr_temperature_c REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS pbr_temperature_ix
ON pbr_temperature (experiment, pioreactor_unit, timestamp);

DROP TABLE IF EXISTS pbr_ph;

CREATE TABLE IF NOT EXISTS pbr_ph (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    pbr_ph_ph REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS pbr_ph_ix
ON pbr_ph (experiment, pioreactor_unit, timestamp);


DROP TABLE IF EXISTS pioreactor_unit_activity_data;

CREATE TABLE IF NOT EXISTS pioreactor_unit_activity_data (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    od_reading REAL,
    normalized_od_reading REAL,
    temperature_c REAL,
    growth_rate REAL,
    measured_rpm REAL,
    led_a_intensity_update REAL,
    led_b_intensity_update REAL,
    led_c_intensity_update REAL,
    led_d_intensity_update REAL,
    add_media_ml REAL,
    remove_waste_ml REAL,
    add_alt_media_ml REAL,
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
    pbr_temperature_c REAL,
    pbr_ph_ph REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (
        experiment
    ) ON DELETE CASCADE
    UNIQUE (experiment, pioreactor_unit, timestamp)
);
