DROP TABLE IF EXISTS lightrod_temperatures;

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

DROP TABLE IF EXISTS pbr_temperature;

CREATE TABLE IF NOT EXISTS pbr_temperature (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    pbr_temperature REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS pbr_temperature_ix
ON pbr_temperature (experiment, pioreactor_unit, timestamp);


DROP TABLE IF EXISTS plot_lightrod_temperatures;

CREATE TABLE IF NOT EXISTS plot_lightrod_temperatures (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    max_temperature REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS plot_lightrod_temperatures_ix
ON plot_lightrod_temperatures (experiment, pioreactor_unit, timestamp);

DROP TABLE IF EXISTS density;

CREATE TABLE IF NOT EXISTS density (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    density REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS density_ix
ON density (experiment, pioreactor_unit, timestamp);


DROP TABLE IF EXISTS absolute_growth_rate;

CREATE TABLE IF NOT EXISTS absolute_growth_rate (
    experiment TEXT NOT NULL,
    pioreactor_unit TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    absolute_growth_rate REAL,
    FOREIGN KEY (experiment) REFERENCES experiments (experiment) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS absolute_growth_rate_ix
ON absolute_growth_rate (experiment, pioreactor_unit, timestamp);

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
    pbr_temperature REAL,
    max_temperature REAL,
    density Real,
    absolute_growth_rate Real,

    FOREIGN KEY (experiment) REFERENCES experiments (
        experiment
    ) ON DELETE CASCADE,
    UNIQUE (experiment, pioreactor_unit, timestamp) -- THIS FUCKING LINE IS IMPORTANT
);