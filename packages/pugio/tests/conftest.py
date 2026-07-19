"""테스트 전용 sys.path 부트스트랩.

--import-mode=importlib(루트 pyproject.toml)에서는 테스트 파일 디렉터리가
자동으로 sys.path에 들어가지 않는다 — `_sink_contract` 같은 이 디렉터리 내
공유 헬퍼 모듈을 형제 테스트 파일들이 bare import할 수 있도록 여기서 명시적으로
등록한다 (M2-F Task F4).
"""

import sys
from pathlib import Path

_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
