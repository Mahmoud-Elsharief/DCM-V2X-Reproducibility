from pathlib import Path
import sys

SIM = Path(__file__).resolve().parents[1] / "simulator"
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))
