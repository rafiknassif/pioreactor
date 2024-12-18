import csv
from datetime import datetime
from growth_rate_calculating import GrowthRateCalculator

# Load pre-recorded OD readings from a CSV file
def load_od_readings(file_path):
    od_readings = []
    with open(file_path, 'r') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            od_readings.append({
                "timestamp": row["timestamp"],  # ISO 8601 timestamp
                "od": float(row["od_reading"]),  # Optical density reading
                "angle": row["angle"],  # Measurement angle
                "channel": row["channel"]  # Photodiode channel
            })
    return od_readings

# Initialize the GrowthRateCalculator
def initialize_growth_rate_calculator():
    unit = "pio"  # Replace with your unit name
    experiment = "ukf_exp"  # Replace with your experiment name
    calculator = GrowthRateCalculator(
        unit=unit,
        experiment=experiment,
        ignore_cache=True,
        source_obs_from_mqtt=False  # We'll supply the data manually
    )
    return calculator

# Sequentially process OD readings without delays
def process_od_readings(od_readings, calculator):
    for reading in od_readings:
        # Convert timestamp to datetime object
        timestamp = datetime.strptime(reading["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ")
        od_value = reading["od"]

        # Create a mock observation structure for the calculator
        observation = {
            "timestamp": timestamp,
            "ods": {
                reading["channel"]: {  # Dynamic channel key
                    "od": od_value,
                    "angle": reading["angle"],
                    "dynamic_zero_offset": 0.0  # Adjust if needed
                }
            }
        }

        # Update the calculator with the new observation
        try:
            growth_rate, od_filtered, kf_outputs = calculator.update_state_from_observation(observation)
            print(f"Processed OD: {od_value} at {timestamp}. Growth Rate: {growth_rate.growth_rate}")
        except Exception as e:
            print(f"Error processing OD reading: {e}")

# Main function
def main():
    od_file = "pre_recorded_od_readings.csv"  # Replace with your OD readings file
    od_readings = load_od_readings(od_file)

    calculator = initialize_growth_rate_calculator()
    process_od_readings(od_readings, calculator)

if __name__ == "__main__":
    main()
