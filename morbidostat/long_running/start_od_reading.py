"""
Continuously take an optical density reading (more accurately: a backscatter reading, which is a proxy for OD).
This script is designed to run in a background process and push data to MQTT.

>>> nohup python3 -m morbidostat.long_running.start_od_reading &
"""
import configparser
import time

import click
from adafruit_ads1x15.analog_in import AnalogIn
import adafruit_ads1x15.ads1115 as ADS
from  paho.mqtt import publish
import board
import busio

from morbidostat.utils.streaming import LowPassFilter



config = configparser.ConfigParser()
config.read('config.ini')


@click.command()
@click.option('--unit', default="1", help='The morbidostat unit')
def start_optical_density(unit):

    verbose = True


    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c, gain=4)
    chan = AnalogIn(ads, ADS.P0, ADS.P1)
    sampling_rate = 1/int(config['od_sampling']['samples_per_second'])
    sm = LowPassFilter(int(config['od_sampling']['samples_per_second']), 0.0001, sampling_rate)

    publish.single(f"morbidostat/{unit}/log", "starting start_od_reading.py")

    i = 0
    while True:
        time.sleep(sampling_rate)
        try:
            raw_signal = chan.voltage
            sm.update(raw_signal)

            if sm.latest_reading is not None and i % int(config['od_sampling']['mqtt_publish_rate']) == 0:
                publish.single(f"morbidostat/{unit}/od_low_pass", sm.latest_reading)
                publish.single(f"morbidostat/{unit}/od_raw", raw_signal)

            if verbose:
                print(raw_signal, sm.latest_reading)

            i+=1

        except OSError as e:
            # just pause, not sure why this happens when add_media or remove_waste are called.
            time.sleep(5.0)
        except Exception as e:
            publish.single(f"morbidostat/{unit}/log", f"start_od_reading.py failed with {str(e)}")
            publish.single(f"morbidostat/{unit}/error_log", f"{unit} start_od_reading.py failed with {str(e)}")
            raise e

if __name__ == '__main__':
    start_optical_density()

