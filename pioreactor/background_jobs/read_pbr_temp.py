from contextlib import suppress
from time import sleep
from pioreactor import exc
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.hardware import NTC_Thermistor_ADDR
from pioreactor.structs import Temperature
from pioreactor.utils.temps import ADS1115_Thermistor
from pioreactor.utils.timing import RepeatedTimer, current_utc_datetime
from pioreactor.config import config
from pioreactor.actions.led_intensity import led_intensity
import click


class ReadPBRTemp(BackgroundJob):
    job_name = "read_pbr_temp"
    published_settings = {
        'upper_warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        'lower_warning_threshold': {'datatype': "float", "unit": "℃", "settable": True},
        "PBR_temp": {"datatype": "Temperature", "settable": False}
    }


    def __init__(self, unit, experiment, upper_warning_threshold=35, lower_warning_threshold=18):
        super().__init__(unit=unit, experiment=experiment)
        self.initializeDrivers(NTC_Thermistor_ADDR)
        self.set_upper_warning_threshold(upper_warning_threshold)
        self.set_lower_warning_threshold(lower_warning_threshold)
        self.PBR_temp = None  # initialize for mqtt broadcast

        dt = 1 / (config.getfloat("pbr_temp_reading.config", "samples_per_second", fallback=0.033))

        self.read_pbr_temperature_timer = RepeatedTimer(
            dt,
            self.read_temp,
            job_name=self.job_name,
            run_immediately=False,
        ).start()

    def initializeDrivers(self, i2c_addr):
        self.ads1115_driver = ADS1115_Thermistor(
            address=i2c_addr,
            r_ref=10000.0,        # 10K reference resistor
            use_steinhart=True    # Use Steinhart-Hart equation for accuracy
        )

    def set_upper_warning_threshold(self, temp_thresh):
        self.upper_warning_threshold = temp_thresh

    def set_lower_warning_threshold(self, temp_thresh):
        self.lower_warning_threshold = temp_thresh

    def read_temp(self):
        temp = self._read_average_temperature()

        self.PBR_temp = Temperature(
            timestamp=current_utc_datetime(),
            temperature=temp,
        )

    def log_PBR_temperature(self):
        self.logger.debug(
            f"PBR Temperature: {self.PBR_temp.temperature}"
        )

    def on_disconnected(self) -> None:
        with suppress(AttributeError):
            self.read_pbr_temperature_timer.cancel()

    ########## Private & internal methods

    def _read_average_temperature(self) -> float:
        """
        Read the current temperature from sensor, in Celsius
        """
        running_sum, running_count = 0.0, 0
        try:
            # check temp is fast, let's do it a few times to reduce variance.
            for i in range(6):
                running_sum += self.ads1115_driver.get_temperature()
                running_count += 1
                sleep(0.05)

        except OSError as e:
            self.logger.debug(e, exc_info=True)
            raise exc.HardwareNotFoundError(
                "Is the NTC thermistor connected to the I2C bus? Unable to find temperature sensor."
            )

        averaged_temp = running_sum / running_count
        self._check_if_exceeds_temp_range(averaged_temp)

        return averaged_temp

    def _check_if_exceeds_temp_range(self, temp: float) -> bool:
        if temp > self.upper_warning_threshold:
            self.logger.warning(
                f"Temperature of thermistor has exceeded {self.upper_warning_threshold}℃ - currently {temp}℃. LEDs will be powered off"
            )

            channel = 'B'
            success = led_intensity(
                {channel: 0},
                unit=self.unit,
                experiment=self.experiment,
                pubsub_client=self.pub_client,
                source_of_event=f"{self.job_name}",
            )
            if success:
                self.logger.warning("lights were turned off due to high temp")
        elif temp < self.lower_warning_threshold:
            self.logger.warning(
                f"Temperature of thermistor has fallen below {self.lower_warning_threshold}℃ - currently {temp}℃. Some action will be taken maybe idk"
            )
            # TODO implement undertemperature correction action

        return temp > self.upper_warning_threshold and temp < self.lower_warning_threshold


@click.command(name="read_pbr_temp")
@click.option(
    "--upper-warning-threshold",
    default=35,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
@click.option(
    "--lower-warning-threshold",
    default=18,
    show_default=True,
    type=click.FloatRange(0, 100, clamp=True),
)
@click.option(
    "--calibration-mode",
    is_flag=True,
    help="Run in calibration mode to measure resistance at different temperatures",
)
def click_read_pbr_temp(upper_warning_threshold, lower_warning_threshold, calibration_mode):
    """
    CALIBRATION MODE INSTRUCTIONS:
    ==============================
    
    To calibrate your thermistor, run:
        pio run read_pbr_temp --calibration-mode
    
    This will continuously print resistance and voltage readings without saving to database.
    
    PROCEDURE:
    1. Prepare three temperature baths:
       - Ice water (0°C): Fill container with ice and water, stir well
       - Room temperature (~25°C): Let water sit at room temp
       - Hot water (50-60°C): Use kettle or stove, measure with reference thermometer
    
    2. For each temperature:
       a. Place thermistor in bath
       b. Wait 2-3 minutes for readings to stabilize
       c. Record the RESISTANCE value (and actual temperature if using thermometer)
       d. Write down: Temperature (°C), Resistance (Ω)
    
    3. You should have three data points like:
       0.0°C  → 32650 Ω
       25.0°C → 10000 Ω
       55.0°C → 3200 Ω
    
    4. Calculate coefficients using Python:
    
    For STEINHART-HART (most accurate):
    -----------------------------------
    import numpy as np
    import math
    
    # Replace with your measurements!
    measurements = [
        (0.0 + 273.15, 32650),   # (Temp in Kelvin, Resistance in Ω)
        (25.0 + 273.15, 10000),
        (55.0 + 273.15, 3200),
    ]
    
    temps = np.array([t for t, r in measurements])
    resistances = np.array([r for t, r in measurements])
    ln_r = np.log(resistances)
    X = np.column_stack([np.ones(len(temps)), ln_r, ln_r**3])
    y = 1.0 / temps
    A, B, C = np.linalg.lstsq(X, y, rcond=None)[0]
    
    print(f"STEINHART_A = {A:.10f}")
    print(f"STEINHART_B = {B:.10f}")
    print(f"STEINHART_C = {C:.10e}")
    
    # Update these values in temps.py in the ADS1115_Thermistor class
    
    For BETA (simpler, less accurate):
    ----------------------------------
    import math
    
    # Use only 2 measurements (typically 25°C and one other)
    T1 = 25.0 + 273.15  # Room temp in Kelvin
    R1 = 10000          # Resistance at T1
    
    T2 = 55.0 + 273.15  # Hot water temp in Kelvin
    R2 = 3200           # Resistance at T2
    
    BETA = math.log(R1/R2) / ((1/T1) - (1/T2))
    
    print(f"BETA = {BETA:.0f}")
    print(f"T0 = {T1}")
    print(f"R0 = {R1}")
    
    # Update these values in temps.py in the ADS1115_Thermistor class
    
    Press Ctrl+C to exit calibration mode.
    """
    
    if calibration_mode:
        # Run in calibration mode - just print resistance continuously
        from pioreactor.whoami import get_unit_name
        from pioreactor.hardware import NTC_Thermistor_ADDR
        from pioreactor.utils.temps import ADS1115_Thermistor
        from time import sleep
        
        print("\n" + "="*60)
        print("THERMISTOR CALIBRATION MODE")
        print("="*60)
        print("Instructions:")
        print("1. Place thermistor in ice water (0°C)")
        print("2. Wait for readings to stabilize (2-3 minutes)")
        print("3. Record the resistance value")
        print("4. Repeat for room temperature (~25°C)")
        print("5. Repeat for hot water (50-60°C, measure with thermometer)")
        print("6. Press Ctrl+C when done")
        print("="*60 + "\n")
        
        try:
            sensor = ADS1115_Thermistor(
                address=NTC_Thermistor_ADDR,
                r_ref=10000.0,
                use_steinhart=True
            )
            
            print("Sensor connected. Starting readings...\n")
            
            while True:
                try:
                    # Read raw values
                    v_therm, v_ref = sensor.get_voltages()
                    resistance = sensor.get_resistance()
                    
                    # Also show what temperature it would calculate (may be wrong if not calibrated)
                    temp = sensor.get_temperature()
                    
                    print(f"Resistance: {resistance:7.0f} Ω  |  "
                          f"Voltages: V_therm={v_therm:.3f}V V_ref={v_ref:.3f}V  |  "
                          f"Current temp reading: {temp:.2f}°C (may be inaccurate)")
                    
                    sleep(2)  # Read every 2 seconds
                    
                except KeyboardInterrupt:
                    print("\n\nCalibration mode ended.")
                    print("Use the resistance values you recorded to calculate coefficients.")
                    print("See the help text above for calculation formulas.")
                    break
                except Exception as e:
                    print(f"Error reading sensor: {e}")
                    sleep(2)
                    
        except Exception as e:
            print(f"Failed to initialize sensor: {e}")
            return
    
    else:
        # Normal operation mode
        from pioreactor.whoami import get_unit_name, get_assigned_experiment_name

        unit = get_unit_name()
        experiment = get_assigned_experiment_name(unit)

        job = ReadPBRTemp(
            upper_warning_threshold=upper_warning_threshold,
            lower_warning_threshold=lower_warning_threshold,
            unit=unit,
            experiment=experiment,
        )
        job.block_until_disconnected()