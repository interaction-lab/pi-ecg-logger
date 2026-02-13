# ecg_logger.py
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

# ----- ADDED: minimal phase/event logging state -----
_output_dir = None
_sample_index = 0
_sample_index_lock = threading.Lock()
_phases_lock = threading.Lock()
_PHASES_FILENAME = "phases.csv"

def _ensure_output_dir(output_path):
    """Remember the output directory so mark_phase() can write there."""
    global _output_dir
    p = Path(output_path)
    if p.suffix:  # looks like a file path -> use parent
        p = p.parent
    _output_dir = p.resolve()
    _output_dir.mkdir(parents=True, exist_ok=True)

def mark_phase(phase_name: str):
    """
    Append a phase event to <output_dir>/phases.csv:
      phase, timestamp_iso, timestamp_unix_ms, sample_index

    Raises RuntimeError if start_logging(...) hasn't been called to set output dir.
    """
    global _sample_index, _output_dir
    if _output_dir is None:
        raise RuntimeError("ECG logger not started: call start_logging(...) first")

    with _sample_index_lock:
        idx = _sample_index

    ts_unix_ms = int(time.time() * 1000)
    ts_iso = datetime.utcnow().isoformat() + "Z"
    phases_path = _output_dir / _PHASES_FILENAME

    with _phases_lock:
        file_exists = phases_path.exists()
        with open(phases_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not file_exists:
                w.writerow(["phase", "timestamp_iso", "timestamp_unix_ms", "sample_index"])
            w.writerow([phase_name, ts_iso, ts_unix_ms, idx])

# ---- END ADDED ----


def _ecg_logging_loop(output_path, sample_rate):
    """
    Background ECG acquisition loop.
    """

    # ---- Hardware init ----
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1
    ads.data_rate = 860
    chan = AnalogIn(ads, 0)

    # ---- Timing setup ----
    sample_period = 1.0 / sample_rate
    start_time = time.monotonic()
    next_sample_time = start_time

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # ensure output dir stored for phase logging
    _ensure_output_dir(output_path)

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        # Header (explicit + machine-readable)
        writer.writerow([
            "sample_index",
            "timestamp_sec",
            "voltage"
        ])
        f.flush()

        # <-- changed to use module-level _sample_index (kept semantics) -->
        global _sample_index
        with _sample_index_lock:
            _sample_index = 0

        while not _stop_event.is_set():
            now = time.monotonic()

            if now >= next_sample_time:
                voltage = chan.voltage
                timestamp = now - start_time

                # capture index, write row, then increment
                with _sample_index_lock:
                    idx = _sample_index

                writer.writerow([
                    idx,
                    f"{timestamp:.6f}",
                    f"{voltage:.6f}"
                ])

                f.flush()

                # increment after writing to preserve previous semantics
                with _sample_index_lock:
                    _sample_index += 1

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
    global _thread, _stop_event, _sample_index

    if _thread and _thread.is_alive():
        raise RuntimeError("ECG logging already running")

    if not (200 <= sample_rate <= 500):
        raise ValueError("Sample rate should be between 200–500 Hz for ECG")

    # Remember output dir for phase logging immediately
    _ensure_output_dir(output_path)

    _stop_event.clear()

    # reset sample index
    with _sample_index_lock:
        _sample_index = 0

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
