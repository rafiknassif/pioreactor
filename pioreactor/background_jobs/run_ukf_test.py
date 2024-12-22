import csv
import json
from datetime import datetime, timezone
from statistics import mean, variance
from pioreactor.utils import local_persistant_storage
from pioreactor.background_jobs.growth_rate_calculating import GrowthRateCalculator
from pioreactor.structs import Dynamic_Offset_ODReadings, Dynamic_Offset_ODReading
from pioreactor import whoami


# Load pre-recorded OD readings from a CSV file
def load_od_readings(file_path):
    od_readings = []
    with open(file_path, 'r') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            od_readings.append({
                "timestamp": row["timestamp"],  # ISO 8601 timestamp
                "od": float(row["od_reading"]),  # Optical density reading
                "channel": row["channel"]  # Photodiode channel
            })
    return od_readings


# Calculate OD statistics (mean and variance)
def calculate_od_statistics(od_readings, num_samples):
    channels = set([reading["channel"] for reading in od_readings])
    mean_per_channel = {}
    variance_per_channel = {}

    for channel in channels:
        channel_readings = [reading["od"] for reading in od_readings if reading["channel"] == channel]
        if len(channel_readings) < num_samples:
            raise ValueError(f"Not enough samples for channel {channel}. Required: {num_samples}, Found: {len(channel_readings)}")
        
        recent_readings = channel_readings[:num_samples]  # Use the first `num_samples` readings
        mean_per_channel[channel] = mean(recent_readings)
        variance_per_channel[channel] = variance(recent_readings)
    
    return mean_per_channel, variance_per_channel


# Main function to override `growth_rate_calculating` behavior
def run_growth_rate_calculation_from_csv(csv_file_path, unit_name, num_samples):
    # Get the current experiment name dynamically
    experiment_name = whoami.get_assigned_experiment_name(unit_name)

    # Load OD readings from CSV
    od_readings = load_od_readings(csv_file_path)

    # Calculate statistics
    mean_per_channel, variance_per_channel = calculate_od_statistics(od_readings, num_samples)

    # Substitute statistics into local persistent storage
    with local_persistant_storage("od_normalization_mean") as cache:
        cache[experiment_name] = json.dumps(mean_per_channel)
    with local_persistant_storage("od_normalization_variance") as cache:
        cache[experiment_name] = json.dumps(variance_per_channel)
    print("OD statistics stored in cache successfully.")

    # Initialize the GrowthRateCalculator
    calculator = GrowthRateCalculator(
        unit=unit_name,
        experiment=experiment_name,
        ignore_cache=False,  # Use cached values calculated earlier
        source_obs_from_mqtt=False  # Override source
    )

    # Process OD readings and simulate the `update_state_from_observation` method
    for reading in od_readings:
        # Convert timestamp to datetime object
        timestamp = datetime.strptime(reading["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        od_value = reading["od"]

        # Create a Dynamic_Offset_ODReadings object
        od_readings_obj = Dynamic_Offset_ODReadings(
            timestamp=timestamp,
            ods={
                reading["channel"]: Dynamic_Offset_ODReading(
                    od=od_value,
                    angle="90",  # Assuming a fixed angle; adjust as needed
                    timestamp=timestamp,
                    channel=reading["channel"],
                    dynamic_zero_offset=0.0  # Adjust if needed
                )
            },
        )

        # Update the state using the pre-recorded observation
        try:
            growth_rate, od_filtered, kf_outputs, absolute_growth_rate, density = calculator.update_state_from_observation(od_readings_obj)
            print(f"Processed OD: {od_value} at {timestamp}. Growth Rate: {growth_rate.growth_rate}")
        except Exception as e:
            print(f"Error processing OD reading: {e}")

    # Ensure all cached data and logs are finalized
    calculator.clean_up()
    print("Completed processing all OD readings from CSV.")


# Example usage
if __name__ == "__main__":
    csv_file_path = "pre_recorded_od_readings.csv"  # Path to the CSV file
    unit_name = whoami.get_unit_name()  # Dynamically detect the current unit
    num_samples = 35  # Number of samples for statistics calculation

    run_growth_rate_calculation_from_csv(csv_file_path, unit_name, num_samples)
