"""결정적 합성 데이터 생성 스크립트. 실제 로직은 augur.fixture.make — 여기는 진입점만.

실행: python packages/augur/eval/make_fixture.py [out_dir]  (기본 ./data)
"""

import sys
from pathlib import Path

from augur.fixture import make

if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./data")
    make(out)
    print(f"fixture written to {out}")
