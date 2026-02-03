import csv
import threading
import time
from datetime import datetime
from pathlib import Path

import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn


# ---- Internal state ----
_thread = None
_stop_event = threading.Event()


def _ecg_logging_loop(output_path, sample_rate):
    """
    Background ECG acquisition loop.
    """

    # ---- Hardware init (INSIDE thread, not at import time) ----
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1
    ads.data_rate = 860  # max stable rate
    chan = AnalogIn(ads, ADS.P0)

    # ---- Timing setup ----
    sample_period = 1.0 / sample_rate
    start_time = time.monotonic()
    next_sample_time = start_time

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        # Header (explicit + machine-readable)
        writer.writerow([
            "sample_index",
            "timestamp_sec",
            "voltage"
        ])
        f.flush()

        sample_index = 0

        while not _stop_event.is_set():
            now = time.monotonic()

            if now >= next_sample_time:
                voltage = chan.voltage
                timestamp = now - start_time

                writer.writerow([
                    sample_index,
                    f"{timestamp:.6f}",
                    f"{voltage:.6f}"
                ])

                sample_index += 1
                next_sample_time += sample_period

            else:
                # Yield CPU briefly to reduce jitter
                time.sleep(0.0002)

        f.flush()


def start_logging(
    output_path="/home/pi/ecg.csv",
    sample_rate=500
):
    """
    Start background ECG logging.
    """

    global _thread

    if _thread and _thread.is_alive():
        raise RuntimeError("ECG logging already running")

    if not (200 <= sample_rate <= 500):
        raise ValueError("Sample rate should be between 200–500 Hz for ECG")

    _stop_event.clear()

    _thread = threading.Thread(
        target=_ecg_logging_loop,
        args=(output_path, sample_rate),
        daemon=True
    )
    _thread.start()


def stop_logging():
    """
    Stop background ECG logging.
    """
    _stop_event.set()
    if _thread:
        _thread.join(timeout=2.0)
