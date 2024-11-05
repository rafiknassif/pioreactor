CREATE TRIGGER IF NOT EXISTS update_pioreactor_unit_activity_data_from_lightrod_temperatures AFTER INSERT ON lightrod_temperatures
BEGIN
    INSERT INTO pioreactor_unit_activity_data(
        pioreactor_unit,
        experiment,
        timestamp,
        lightrod_top_temp,
        lightrod_middle_temp,
        lightrod_bottom_temp
    ) VALUES (
        new.pioreactor_unit,
        new.experiment,
        new.timestamp,
        new.top_temp,
        new.middle_temp,
        new.bottom_temp
    )
    ON CONFLICT(experiment, pioreactor_unit, timestamp) DO UPDATE SET
        lightrod_top_temp=excluded.lightrod_top_temp,
        lightrod_middle_temp=excluded.lightrod_middle_temp,
        lightrod_bottom_temp=excluded.lightrod_bottom_temp;
END;
