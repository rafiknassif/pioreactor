import csv
import json
from statistics import mean, variance
from pioreactor.utils import local_persistant_storage

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
    """
    Calculates the mean and variance of OD readings for each photodiode channel.

    Args:
        od_readings (list): A list of OD readings dictionaries.
        num_samples (int): Number of samples to use for the calculation.

    Returns:
        tuple: (mean_per_channel, variance_per_channel)
    """
    channels = set([reading["channel"] for reading in od_readings])
    mean_per_channel = {}
    variance_per_channel = {}

    for channel in channels:
        channel_readings = [reading["od"] for reading in od_readings if reading["channel"] == channel]
        if len(channel_readings) < num_samples:
            raise ValueError(f"Not enough samples for channel {channel}. Required: {num_samples}, Found: {len(channel_readings)}")
        
        # Use the most recent `num_samples` readings
        recent_readings = channel_readings[-num_samples:]
        mean_per_channel[channel] = mean(recent_readings)
        variance_per_channel[channel] = variance(recent_readings)
    
    return mean_per_channel, variance_per_channel

# Store statistics in the appropriate cache
def store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment):
    """
    Stores the computed statistics in the appropriate local persistent storage caches.

    Args:
        mean_per_channel (dict): Mean OD values per channel.
        variance_per_channel (dict): Variance OD values per channel.
        experiment (str): Experiment name for the cache.
    """
    # Store mean values
    with local_persistant_storage("od_normalization_mean") as cache:
        cache[experiment] = json.dumps(mean_per_channel)
    
    # Store variance values
    with local_persistant_storage("od_normalization_variance") as cache:
        cache[experiment] = json.dumps(variance_per_channel)
    
    print("OD statistics stored in cache successfully.")

# Main function
def main():
    # Define parameters
    csv_file_path = "pre_recorded_od_readings.csv"  # Path to the CSV file
    experiment_name = "experiment1"  # Name of the experiment
    num_samples = 35  # Number of samples to use for OD statistics calculation

    # Load OD readings from the CSV
    od_readings = load_od_readings(csv_file_path)

    # Calculate OD statistics
    mean_per_channel, variance_per_channel = calculate_od_statistics(od_readings, num_samples)

    # Store statistics in the cache
    store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment_name)

    print("Mean per channel:", mean_per_channel)
    print("Variance per channel:", variance_per_channel)

if __name__ == "__main__":
    main()
