# -*- coding: utf-8 -*-
"""
This job runs on the leader, and is a replacement for the NodeRed database streaming job.
"""
import signal
import os
import click
import json
from collections import namedtuple


from pioreactor.pubsub import QOS
from pioreactor.background_jobs.base import BackgroundJob
from pioreactor.whoami import get_unit_name, UNIVERSAL_EXPERIMENT
from pioreactor.config import config
from pioreactor.utils.timing import current_utc_time

JOB_NAME = os.path.splitext(os.path.basename((__file__)))[0]

SetAttrSplitTopic = namedtuple(
    "SetAttrSplitTopic", ["pioreactor_unit", "experiment", "timestamp"]
)
Metadata = namedtuple("Metadata", ["topic", "table", "parser"])


class MqttToDBStreamer(BackgroundJob):
    def __init__(self, topics_and_parsers, **kwargs):

        from sqlite3worker import Sqlite3Worker

        super(MqttToDBStreamer, self).__init__(job_name=JOB_NAME, **kwargs)
        self.sqliteworker = Sqlite3Worker(
            config["storage"]["database"], max_queue_size=250, raise_on_error=False
        )
        self.topics_and_callbacks = [
            {
                "topic": topic_and_parser.topic,
                "callback": self.create_on_message(topic_and_parser),
            }
            for topic_and_parser in topics_and_parsers
        ]

        self.start_passive_listeners()

    def on_disconnect(self):
        self.sqliteworker.close()  # close the db safely

    def create_on_message(self, topic_and_parser):
        def _callback(message):
            cols_to_values = topic_and_parser.parser(message.topic, message.payload)

            cols_placeholder = ", ".join(cols_to_values.keys())
            values_placeholder = ", ".join([":" + c for c in cols_to_values.keys()])
            SQL = f"""INSERT INTO {topic_and_parser.table} ({cols_placeholder}) VALUES ({values_placeholder})"""
            self.sqliteworker.execute(SQL, cols_to_values)

        return _callback

    def start_passive_listeners(self):
        for topic_and_callback in self.topics_and_callbacks:
            self.subscribe_and_callback(
                topic_and_callback["callback"],
                topic_and_callback["topic"],
                qos=QOS.EXACTLY_ONCE,
                allow_retained=False,
            )


def produce_metadata(topic):
    split_topic = topic.split("/")
    return (
        SetAttrSplitTopic(split_topic[1], split_topic[2], current_utc_time()),
        split_topic,
    )


###################
# parsers
###################


def parse_od(topic, payload):
    metadata, split_topic = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "od_reading_v": float(payload),
        "angle": split_topic[-2],
        "channel": split_topic[-1],
    }


def parse_od_filtered(topic, payload):
    metadata, split_topic = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "normalized_od_reading": float(payload),
        "angle": split_topic[-2],
        "channel": split_topic[-1],
    }


def parse_dosing_events(topic, payload):
    payload = json.loads(payload)
    metadata, _ = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "volume_change_ml": payload["volume_change"],
        "event": payload["event"],
        "source_of_event": payload["source_of_event"],
    }


def parse_led_events(topic, payload):
    payload = json.loads(payload)
    metadata, _ = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "channel": payload["channel"],
        "intensity": payload["intensity"],
        "event": payload["event"],
        "source_of_event": payload["source_of_event"],
    }


def parse_growth_rate(topic, payload):
    metadata, _ = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "rate": float(payload),
    }


def parse_temperature(topic, payload):
    metadata, _ = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "temperature_c": float(payload),
    }


def parse_pid_logs(topic, payload):
    metadata, _ = produce_metadata(topic)
    payload = json.loads(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "setpoint": payload["setpoint"],
        "output_limits_lb": payload["output_limits_lb"],
        "output_limits_ub": payload["output_limits_ub"],
        "Kd": payload["Kd"],
        "Ki": payload["Ki"],
        "Kp": payload["Kp"],
        "integral": payload["integral"],
        "proportional": payload["proportional"],
        "derivative": payload["derivative"],
        "latest_input": payload["latest_input"],
        "latest_output": payload["latest_output"],
        "job_name": payload["job_name"],
        "target_name": payload["target_name"],
    }


def parse_alt_media_fraction(topic, payload):
    metadata, _ = produce_metadata(topic)

    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "alt_media_fraction": float(payload),
    }


def parse_logs(topic, payload):
    metadata, split_topic = produce_metadata(topic)
    payload = json.loads(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "message": payload["message"],
        "task": payload["task"],
        "level": payload["level"],
        "source": split_topic[-1],  # should be app, ui, etc.
    }


def parse_kalman_filter_outputs(topic, payload):
    metadata, _ = produce_metadata(topic)
    payload = json.loads(payload)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "state": json.dumps(payload["state"]),
        "covariance_matrix": json.dumps(payload["covariance_matrix"]),
    }


def parse_automation_settings(topic, payload):
    payload = json.loads(payload.decode())
    return payload


def parse_stirring_rates(topic, payload):
    metadata = produce_metadata(topic)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "rpm": float(payload),
    }


def parse_od_statistics(topic, payload):
    metadata, split_topic = produce_metadata(topic)
    return {
        "experiment": metadata.experiment,
        "pioreactor_unit": metadata.pioreactor_unit,
        "timestamp": metadata.timestamp,
        "source": split_topic[-2],
        "estimator": split_topic[-1],
        "estimate": float(payload),
    }


def mqtt_to_db_streaming():
    topics_and_parsers = [
        Metadata(
            "pioreactor/+/+/growth_rate_calculating/od_filtered/+/+",
            "od_readings_filtered",
            parse_od_filtered,
        ),
        Metadata("pioreactor/+/+/od_reading/od_raw/+/+", "od_readings_raw", parse_od),
        Metadata("pioreactor/+/+/dosing_events", "dosing_events", parse_dosing_events),
        Metadata("pioreactor/+/+/led_events", "led_events", parse_led_events),
        Metadata(
            "pioreactor/+/+/growth_rate_calculating/growth_rate",
            "growth_rates",
            parse_growth_rate,
        ),
        Metadata(
            "pioreactor/+/+/temperature_control/temperature",
            "temperature_readings",
            parse_temperature,
        ),
        Metadata("pioreactor/+/+/pid_log", "pid_logs", parse_pid_logs),
        Metadata(
            "pioreactor/+/+/alt_media_calculating/alt_media_fraction",
            "alt_media_fraction",
            parse_alt_media_fraction,
        ),
        Metadata("pioreactor/+/+/logs/+", "logs", parse_logs),
        Metadata(
            "pioreactor/+/+/dosing_automation/dosing_automation_settings",
            "dosing_automation_settings",
            parse_automation_settings,
        ),
        Metadata(
            "pioreactor/+/+/led_automation/led_automation_settings",
            "led_automation_settings",
            parse_automation_settings,
        ),
        Metadata(
            "pioreactor/+/+/temperature_automation/temperature_automation_settings",
            "temperature_automation_settings",
            parse_automation_settings,
        ),
        Metadata(
            "pioreactor/+/+/growth_rate_calculating/kalman_filter_outputs",
            "kalman_filter_outputs",
            parse_kalman_filter_outputs,
        ),
        Metadata("pioreactor/+/+/stirring/rpm", "stirring_rates", parse_stirring_rates),
        Metadata("pioreactor/+/od_blank/+", "od_reading_statistics", parse_od_statistics),
        Metadata(
            "pioreactor/+/od_normalization/+",
            "od_reading_statistics",
            parse_od_statistics,
        ),
    ]

    streamer = MqttToDBStreamer(  # noqa: F841
        topics_and_parsers, experiment=UNIVERSAL_EXPERIMENT, unit=get_unit_name()
    )

    while True:
        signal.pause()


@click.command(name="mqtt_to_db_streaming")
def click_mqtt_to_db_streaming():
    """
    (leader only) Send MQTT streams to the database. Parsers should return a dict of all the entries in the corresponding table.
    """
    mqtt_to_db_streaming()
