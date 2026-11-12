"""spikes 에서 scripts/_rawdata.py 를 재사용하기 위한 경로 주입."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _rawdata import COREVIA, RAW, ROOT, read_pipe_csv  # noqa: E402,F401
