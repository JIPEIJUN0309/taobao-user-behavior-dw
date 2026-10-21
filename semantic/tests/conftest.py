import sys
from pathlib import Path

# 让 `pytest semantic/tests` 在任意工作目录下都能 import semantic
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
