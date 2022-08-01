# -*- coding: utf-8 -*-
from __future__ import annotations

from pioreactor.types import GpioPin
from pioreactor.types import PwmChannel
from pioreactor.version import hardware_version_info
from pioreactor.whoami import is_testing_env

# All GPIO pins below are BCM numbered

# Heater PWM
HEATER_PWM_TO_PIN: PwmChannel = "5"

# map between PCB labels and GPIO pins
PWM_TO_PIN: dict[PwmChannel, GpioPin] = {
    "1": 6 if hardware_version_info == (0, 1) else 17,
    "2": 13,  # hardware PWM1 available
    "3": 16,
    "4": 12,  # hardware PWM0 available
    HEATER_PWM_TO_PIN: 18,  # dedicated to heater
}

# led and button GPIO pins
PCB_LED_PIN: GpioPin = 23
PCB_BUTTON_PIN: GpioPin = 24

# RaspAP trigger. See docs: TODO
RASPAP_TRIGGER_ON_PIN: GpioPin = 20
RASPAP_TRIGGER_OFF_PIN: GpioPin = 26

# hall sensor
HALL_SENSOR_PIN: GpioPin = 25

# I2C GPIO pins
SDA: GpioPin = 2
SCL: GpioPin = 3

# I2C channels used
ADC = 0x48  # hex(72)
DAC = 0x49  # hex(73)
TEMP = 0x4F  # hex(79)


def is_HAT_present() -> bool:
    if is_testing_env():
        from pioreactor.utils.mock import MockI2C as I2C
    else:
        from busio import I2C  # type: ignore

    from adafruit_bus_device.i2c_device import I2CDevice  # type: ignore

    with I2C(SCL, SDA) as i2c:
        try:
            I2CDevice(i2c, DAC, probe=True)  # DAC, so we don't interfere with the ADC.
            present = True
        except ValueError:
            present = False
    return present


def is_heating_pcb_present() -> bool:
    if is_testing_env():
        from pioreactor.utils.mock import MockI2C as I2C
    else:
        from busio import I2C  # type: ignore

    from adafruit_bus_device.i2c_device import I2CDevice  # type: ignore

    with I2C(SCL, SDA) as i2c:
        try:
            I2CDevice(i2c, TEMP, probe=True)
            present = True
        except ValueError:
            present = False

    return present


def round_to_half_interger(x):
    y = round(x * 2) / 2
    return y


def voltage_in_aux() -> float:
    # this _can_ mess with OD readings if running at the same time.
    from busio import I2C
    from adafruit_ads1x15.ads1115 import ADS1115, P3
    from adafruit_ads1x15.analog_in import AnalogIn

    slope = 0.1325

    with I2C(SCL, SDA) as i2c:
        ads = ADS1115(i2c, address=ADC, gain=1)
        chan = AnalogIn(ads, P3)
        return round_to_half_interger(chan.voltage / slope)
