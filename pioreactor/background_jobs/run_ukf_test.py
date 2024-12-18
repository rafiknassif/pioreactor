import csv
import json
from datetime import datetime, timezone
from statistics import mean, variance
from pioreactor.utils import local_persistant_storage
from pioreactor.background_jobs.growth_rate_calculating import GrowthRateCalculator
from pioreactor.structs import Dynamic_Offset_ODReadings, Dynamic_Offset_ODReading

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

# Calculate OD statistics
def calculate_od_statistics(od_readings, num_samples):
    channels = set([reading["channel"] for reading in od_readings])
    mean_per_channel = {}
    variance_per_channel = {}

    for channel in channels:
        channel_readings = [reading["od"] for reading in od_readings if reading["channel"] == channel]
        if len(channel_readings) < num_samples:
            raise ValueError(f"Not enough samples for channel {channel}. Required: {num_samples}, Found: {len(channel_readings)}")
        
        recent_readings = channel_readings[-num_samples:]
        mean_per_channel[channel] = mean(recent_readings)
        variance_per_channel[channel] = variance(recent_readings)
    
    return mean_per_channel, variance_per_channel

# Store statistics in the appropriate cache
def store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment):
    with local_persistant_storage("od_normalization_mean") as cache:
        cache[experiment] = json.dumps(mean_per_channel)
    with local_persistant_storage("od_normalization_variance") as cache:
        cache[experiment] = json.dumps(variance_per_channel)
    print("OD statistics stored in cache successfully.")

# Initialize the GrowthRateCalculator
def initialize_growth_rate_calculator(unit, experiment):
    calculator = GrowthRateCalculator(
        unit=unit,
        experiment=experiment,
        ignore_cache=False,  # Use cached values calculated earlier
        source_obs_from_mqtt=False  # We'll supply the data manually
    )
    return calculator

# Sequentially pass OD readings to the growth rate calculator
def process_od_readings(od_readings, calculator):
    for reading in od_readings:
        # Convert timestamp to datetime object and ensure it has a timezone
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

        # Pass the observation object to the calculator
        try:
            growth_rate, od_filtered, kf_outputs = calculator.update_state_from_observation(od_readings_obj)
            print(f"Processed OD: {od_value} at {timestamp}. Growth Rate: {growth_rate.growth_rate}")
        except Exception as e:
            print(f"Error processing OD reading: {e}")

    # Ensure all cached data and logs are finalized
    calculator.clean_up()
    print("Completed processing all OD readings.")

# Main function
def main():
    # Parameters
    csv_file_path = "pre_recorded_od_readings.csv"  # Path to the CSV file
    experiment_name = "experiment1"  # Name of the experiment
    unit_name = "pio"  # Unit name
    num_samples = 35  # Number of samples to use for OD statistics calculation

    # Load OD readings
    od_readings = load_od_readings(csv_file_path)

    # Calculate OD statistics
    mean_per_channel, variance_per_channel = calculate_od_statistics(od_readings, num_samples)

    # Store OD statistics in the cache
    store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment_name)

    # Initialize the growth rate calculator
    calculator = initialize_growth_rate_calculator(unit_name, experiment_name)

    # Sequentially process OD readings through the growth rate calculator
    process_od_readings(od_readings, calculator)

if __name__ == "__main__":
    main()
