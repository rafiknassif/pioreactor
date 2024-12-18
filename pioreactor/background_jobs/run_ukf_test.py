import csv
import json
from statistics import mean, variance
from pioreactor.utils import local_persistant_storage
from pioreactor.pubsub import publish


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
        
        recent_readings = channel_readings[:num_samples]  # Use the first `num_samples` readings
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


# Publish data to MQTT
def publish_to_mqtt(reading, experiment, unit):
    """
    Publish the OD reading to the appropriate MQTT topic.
    """
    topic = f"pioreactor/{unit}/{experiment}/od_reading/ods"
    payload = {
        "ods": {
            reading["channel"]: {
                "od": reading["od"],
                "angle": "90",  # Adjust if needed
                "timestamp": reading["timestamp"],  # Include reading-specific timestamp
                "channel": reading["channel"],  # Include the channel explicitly
                "dynamic_zero_offset": 0.0  # Adjust if needed
            }
        },
        "timestamp": reading["timestamp"]  # Overall payload timestamp
    }
    publish(topic, json.dumps(payload), retain=False)
    print(f"Published to MQTT: {payload}")


# Send OD readings to MQTT without delays
def process_od_readings_with_mqtt_no_delay(od_readings, experiment, unit):
    """
    Send OD readings to MQTT without delays, preserving timestamps.
    """
    for reading in od_readings:
        publish_to_mqtt(reading, experiment, unit)
    print("All OD readings have been sent to MQTT.")


# Main function
def main():
    # Parameters
    csv_file_path = "pre_recorded_od_readings.csv"  # Path to the CSV file
    experiment_name = "experiment1"  # Name of the experiment
    unit_name = "pio"  # Unit name
    num_samples = 35  # Number of samples to use for OD statistics calculation

    print("Ensure that the `growth_rate_calculating` job is active and READY.")
    input("Press Enter once you've confirmed that `growth_rate_calculating` is running and READY...")

    # Load OD readings
    od_readings = load_od_readings(csv_file_path)

    # Calculate OD statistics
    mean_per_channel, variance_per_channel = calculate_od_statistics(od_readings, num_samples)

    # Store OD statistics in the cache
    store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment_name)

    # Send OD readings to MQTT
    process_od_readings_with_mqtt_no_delay(od_readings, experiment_name, unit_name)


if __name__ == "__main__":
    main()
