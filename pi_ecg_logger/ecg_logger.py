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

# ----- ADDED / MODIFIED: minimal phase/event logging state -----
_output_dir = None
_sample_index = 0
_sample_index_lock = threading.Lock()
_phases_lock = threading.Lock()
# now computed per-run (None until start_logging sets it)
_PHASES_FILENAME = None

def _ensure_output_dir_and_phasefile(output_base):
    """
    Remember the output directory and compute phase filename so mark_phase() can write there.

    output_base: Path (may include parent directories) but without suffix (or with - we strip).
    Sets global _output_dir and _PHASES_FILENAME.
    """
    global _output_dir, _PHASES_FILENAME

    base = Path(output_base)
    # If user passed something like "/path/to/ecg_20260213_120000.csv", strip suffix
    if base.suffix:
        base = base.with_suffix('')
    _output_dir = base.parent.resolve()
    _output_dir.mkdir(parents=True, exist_ok=True)

    stem = base.name
    _PHASES_FILENAME = f"{stem}_phases.csv"

def mark_phase(phase_name: str):
    """
    Append a phase event to <output_dir>/<base>_phases.csv:
      phase, timestamp_iso, timestamp_unix_ms, sample_index

    Raises RuntimeError if start_logging(...) hasn't been called to set output dir.
    """
    global _sample_index, _output_dir, _PHASES_FILENAME
    if _output_dir is None or _PHASES_FILENAME is None:
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

# ---- END ADDED / MODIFIED ----


def _ecg_logging_loop(signal_path, sample_rate):
    """
    Background ECG acquisition loop.

    signal_path: full path to the signal CSV file (Path or str)
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

    signal_path = Path(signal_path)
    signal_path.parent.mkdir(parents=True, exist_ok=True)

    # ensure output dir stored for phase logging - this was already done by start_logging,
    # but keep consistent behavior (no-op if already set)
    _ensure_output_dir_and_phasefile(signal_path)

    with open(signal_path, "w", newline="") as f:
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
    output_base="/home/pi/ecg",
    sample_rate=500
):
    """
    Start background ECG logging.

    output_base: base path (directory + base filename) WITHOUT suffix, for example:
        "/home/pi/ecg_20260213_120000"
    If the user passes a .csv suffix (e.g. "/home/pi/ecg_20260213_120000.csv"),
    the suffix will be stripped automatically.

    This will create two files:
      <output_base>_signal.csv   -- sample rows
      <output_base>_phases.csv   -- phase/event rows

    (For backward compatibility this replaces the older single-output_path API.)
    """
    global _thread, _stop_event, _sample_index

    if _thread and _thread.is_alive():
        raise RuntimeError("ECG logging already running")

    if not (200 <= sample_rate <= 500):
        raise ValueError("Sample rate should be between 200–500 Hz for ECG")

    # Prepare base paths
    base = Path(output_base)
    if base.suffix:
        base = base.with_suffix('')  # strip .csv if given

    # Ensure directory exists and remember phase filename
    _ensure_output_dir_and_phasefile(base)

    # compute actual full paths
    signal_path = _output_dir / f"{base.name}_signal.csv"
    # phases file name stored in _PHASES_FILENAME by _ensure_output_dir_and_phasefile

    _stop_event.clear()

    # reset sample index
    with _sample_index_lock:
        _sample_index = 0

    _thread = threading.Thread(
        target=_ecg_logging_loop,
        args=(signal_path, sample_rate),
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
