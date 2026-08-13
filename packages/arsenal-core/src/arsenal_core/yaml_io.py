"""YAML 에러 → 사람이 읽는 한 줄 요약. 로더 3종(pugio/gladius/arsenal) 공용.

pyyaml의 str(YAMLError)는 캐럿이 그려진 여러 줄 덤프라 CLI 에러로는 시끄럽다 —
문제 설명과 위치만 남겨 FatalError 한 줄에 담는다.
"""

import yaml


def yaml_error_summary(e: yaml.YAMLError) -> str:
    """문제 설명 + (line, column) 한 줄. 위치 정보가 없으면 str(e) 그대로."""
    if isinstance(e, yaml.MarkedYAMLError) and e.problem is not None:
        mark = e.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        context = f"{e.context}: " if e.context else ""
        return f"{context}{e.problem}{where}"
    return str(e)
