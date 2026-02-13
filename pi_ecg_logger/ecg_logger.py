# ecg_logger.py
import csv
import threading
import time
from pathlib import Path

import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ---- Module state ----
_thread = None
_stop_event = threading.Event()

_output_dir = None        # Path object
_base = None              # base filename stem (no suffix)
_signal_path = None       # Path to <base>_signal.csv
_phases_path = None       # Path to <base>_phases.csv

_sample_index = 0
_sample_index_lock = threading.Lock()
_phases_lock = threading.Lock()

# monotonic start time used for timestamps in both files
_start_time = None


def mark_phase(phase_name: str):
    """
    Append a phase event to the phases CSV:
      phase, timestamp_sec, sample_index

    timestamp_sec is seconds since logger start (same clock as the signal file).
    Raises RuntimeError if logging hasn't been started.
    """
    global _sample_index, _phases_path, _start_time
    if _phases_path is None or _start_time is None:
        raise RuntimeError("ECG logger not started: call start_logging(...) first")

    with _sample_index_lock:
        idx = _sample_index

    timestamp_sec = time.monotonic() - _start_time
    ts_str = f"{timestamp_sec:.6f}"

    with _phases_lock:
        with open(_phases_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([phase_name, ts_str, idx])


def _ecg_logging_loop(signal_path: Path, sample_rate: int):
    """
    Background loop that samples the analog channel and appends to the signal CSV.
    Uses the module-level _start_time set by start_logging().
    """
    # hardware init
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    ads.gain = 1
    ads.data_rate = 860
    chan = AnalogIn(ads, 0)

    sample_period = 1.0 / float(sample_rate)

    # use the shared start time (start_logging sets it before thread start)
    global _start_time
    if _start_time is None:
        _start_time = time.monotonic()
    start_time = _start_time
    next_sample = start_time

    signal_path.parent.mkdir(parents=True, exist_ok=True)

    with open(signal_path, "a", newline="") as f:
        writer = csv.writer(f)
        # we already wrote headers in start_logging; open in append mode to add rows
        global _sample_index
        with _sample_index_lock:
            _sample_index = 0

        while not _stop_event.is_set():
            now = time.monotonic()
            if now >= next_sample:
                voltage = chan.voltage
                timestamp = now - start_time

                with _sample_index_lock:
                    idx = _sample_index

                writer.writerow([idx, f"{timestamp:.6f}", f"{voltage:.6f}"])
                f.flush()

                with _sample_index_lock:
                    _sample_index += 1

                next_sample += sample_period
            else:
                # yield a tiny bit to reduce jitter
                time.sleep(0.0002)


def start_logging(output_base="/home/pi/ecg", sample_rate=500):
    """
    Start background ECG logging.

    output_base: base path (directory + base filename) WITHOUT suffix, e.g.
      "/home/pi/ecg_20260213_120000"
    If output_base includes a .csv suffix it will be stripped.

    This function creates:
      <output_base>_signal.csv   (headers written)
      <output_base>_phases.csv   (headers written)
    and starts a background thread to append samples to the signal CSV.
    """
    global _thread, _stop_event
    global _output_dir, _base, _signal_path, _phases_path, _start_time, _sample_index

    if _thread and _thread.is_alive():
        raise RuntimeError("ECG logging already running")

    if not (200 <= sample_rate <= 500):
        raise ValueError("Sample rate should be between 200–500 Hz for ECG")

    base = Path(output_base)
    if base.suffix:
        base = base.with_suffix('')
    _output_dir = base.parent.resolve()
    _output_dir.mkdir(parents=True, exist_ok=True)
    _base = base.name
    _signal_path = _output_dir / f"{_base}_signal.csv"
    _phases_path = _output_dir / f"{_base}_phases.csv"

    _stop_event.clear()

    # reset sample index and set start_time BEFORE starting the thread
    with _sample_index_lock:
        _sample_index = 0
    _start_time = time.monotonic()

    # write/truncate both files and their headers synchronously
    with open(_signal_path, "w", newline="", encoding="utf-8") as sf:
        sw = csv.writer(sf)
        sw.writerow(["sample_index", "timestamp_sec", "voltage"])
        sf.flush()

    with _phases_lock:
        with open(_phases_path, "w", newline="", encoding="utf-8") as pf:
            pw = csv.writer(pf)
            pw.writerow(["phase", "timestamp_sec", "sample_index"])
            pf.flush()

    # start background thread (it will append to signal file)
    _thread = threading.Thread(
        target=_ecg_logging_loop,
        args=(_signal_path, sample_rate),
        daemon=True,
    )
    _thread.start()


def stop_logging():
    """
    Stop background ECG logging and clear shared start time.
    """
    global _start_time
    _stop_event.set()
    if _thread:
        _thread.join(timeout=2.0)
    _start_time = None
