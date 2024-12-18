import csv
import json
import time
from datetime import datetime, timezone
from pioreactor.pubsub import publish, subscribe, QOS
from pioreactor.utils import local_persistant_storage
from statistics import mean, variance

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


def check_growth_rate_calculating_ready(experiment, timeout=60):
    """
    Checks if the `growth_rate_calculating` job is active by subscribing to its status topic.
    """
    status_topic = f"pioreactor/{experiment}/growth_rate_calculating/state"
    print(f"Subscribing to topic: {status_topic}")
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            message = subscribe(status_topic, qos=QOS.AT_LEAST_ONCE, timeout=5)
            if message:
                payload = message.payload.decode()
                print(f"Received payload: {payload}")
                if payload.strip().upper() == "READY":
                    print("growth_rate_calculating is READY.")
                    return True
                else:
                    print("Job not ready yet, payload does not match 'READY'.")
            else:
                print("No message received yet...")
        except Exception as e:
            print(f"Error while checking status: {e}")

        time.sleep(5)

    raise TimeoutError("growth_rate_calculating did not initialize within the timeout period.")


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
                "dynamic_zero_offset": 0.0  # Adjust if needed
            }
        },
        "timestamp": reading["timestamp"]
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

    # Load OD readings
    od_readings = load_od_readings(csv_file_path)

    # Calculate OD statistics
    mean_per_channel, variance_per_channel = calculate_od_statistics(od_readings, num_samples)

    # Store OD statistics in the cache
    store_statistics_in_cache(mean_per_channel, variance_per_channel, experiment_name)

    # Check if growth_rate_calculating is ready
    check_growth_rate_calculating_ready(experiment_name)

    # Send OD readings to MQTT
    process_od_readings_with_mqtt_no_delay(od_readings, experiment_name, unit_name)


if __name__ == "__main__":
    main()
