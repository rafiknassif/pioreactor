'''
need to run following commands to make changes:
cd into pioreactor directory
git stash
git pull
git status

chmod +x install-db.sh
./install-db.sh
'''
DROP TRIGGER IF EXISTS update_pioreactor_unit_activity_data_from_lightrod_temperatures;

CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_lightrod_temperatures AFTER INSERT ON lightrod_temperatures
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        LR_A_top_temp,
        LR_A_middle_temp,
        LR_A_bottom_temp,
        LR_A_timestamp,
        LR_B_top_temp,
        LR_B_middle_temp,
        LR_B_bottom_temp,
        LR_B_timestamp,
        LR_C_top_temp,
        LR_C_middle_temp,
        LR_C_bottom_temp,
        LR_C_timestamp
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.LR_A_top_temp,
        new.LR_A_middle_temp,
        new.LR_A_bottom_temp,
        new.LR_A_timestamp,
        new.LR_B_top_temp,
        new.LR_B_middle_temp,
        new.LR_B_bottom_temp,
        new.LR_B_timestamp,
        new.LR_C_top_temp,
        new.LR_C_middle_temp,
        new.LR_C_bottom_temp,
        new.LR_C_timestamp
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        LR_A_top_temp=excluded.LR_A_top_temp,
        LR_A_middle_temp=excluded.LR_A_middle_temp,
        LR_A_bottom_temp=excluded.LR_A_bottom_temp,
        LR_A_timestamp=excluded.LR_A_timestamp,
        LR_B_top_temp=excluded.LR_B_top_temp,
        LR_B_middle_temp=excluded.LR_B_middle_temp,
        LR_B_bottom_temp=excluded.LR_B_bottom_temp,
        LR_B_timestamp=excluded.LR_B_timestamp,
        LR_C_top_temp=excluded.LR_C_top_temp,
        LR_C_middle_temp=excluded.LR_C_middle_temp,
        LR_C_bottom_temp=excluded.LR_C_bottom_temp,
        LR_C_timestamp=excluded.LR_C_timestamp;
END;


DROP TRIGGER IF EXISTS update_pioreactor_unit_activity_data_from_pbr_temperature;

CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_pbr_temperature AFTER INSERT ON pbr_temperature
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        pbr_temperature
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.pbr_temperature
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        pbr_temperature=excluded.pbr_temperature;
END;


DROP TRIGGER IF EXISTS update_pioreactor_unit_activity_data_from_plot_lightrod_temperatures;

CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_plot_lightrod_temperatures AFTER INSERT ON plot_lightrod_temperatures
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        max_temperature
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.max_temperature
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        max_temperature=excluded.max_temperature;
END;


DROP TRIGGER IF EXISTS update_pioreactor_unit_activity_data_from_density;

CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_density AFTER INSERT ON density
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        density
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.density
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        density=excluded.density;
END;


DROP TRIGGER IF EXISTS update_pioreactor_unit_activity_data_from_absolute_growth_rate;

CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_absolute_growth_rate AFTER INSERT ON absolute_growth_rate
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        absolute_growth_rate
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.absolute_growth_rate
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        absolute_growth_rate=excluded.absolute_growth_rate;
END;