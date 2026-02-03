# pi-ecg-logger

Background ECG data logger for Raspberry Pi using AD8232 + ADS1115.

## Installation

```bash
pip install git+https://github.com/interaction-lab/pi-ecg-logger.git
```

## Usage
```python
from pi_ecg_logger import start_logging, stop_logging

start_logging(
    output_path="/home/pi/ecg.csv",
    sample_rate=500
)

# run other code
# ...

stop_logging()
```
