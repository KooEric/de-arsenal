"""루트 conftest의 `docker_available()` 가드 — 컨테이너를 실제로 띄우지 않고
"이 러너에서 Linux 컨테이너를 띄울 수 있는가"만 판정하는지 검증한다.

이 가드가 틀리면 CI가 skip 대신 error로 떨어진다 (Windows 러너에서 실제로 발생:
데몬은 응답하지만 Windows 컨테이너 모드라 postgres 이미지·ryuk의 유닉스 소켓
마운트가 `invalid volume specification`으로 실패했다).

docker SDK 경계는 목으로 대체한다 — 러너에 데몬이 있든 없든 판정 로직 자체를
결정적으로 검증하기 위한 유일한 방법이다.
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

# `import conftest`로는 못 가져온다 — packages/pugio/tests/conftest.py도 같은
# 모듈명이라 수집 순서에 따라 그쪽이 잡힌다. 루트 conftest를 경로로 명시 로드한다.
_ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
_spec = importlib.util.spec_from_file_location("_root_conftest", _ROOT_CONFTEST)
assert _spec is not None and _spec.loader is not None
_root_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root_conftest)
docker_available = _root_conftest.docker_available


class _FakeClient:
    def __init__(self, info: dict[str, Any] | None = None, ping_error: Exception | None = None):
        self._info = info if info is not None else {"OSType": "linux"}
        self._ping_error = ping_error

    def ping(self) -> bool:
        if self._ping_error is not None:
            raise self._ping_error
        return True

    def info(self) -> dict[str, Any]:
        return self._info


def _patch_from_env(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    docker = pytest.importorskip("docker")
    monkeypatch.setattr(docker, "from_env", lambda: client)


def test_linux_container_daemon_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_from_env(monkeypatch, _FakeClient({"OSType": "linux"}))

    assert docker_available() is True


def test_windows_container_daemon_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """데몬은 살아 있지만 Windows 컨테이너 모드 — Linux 이미지를 띄울 수 없다."""
    _patch_from_env(monkeypatch, _FakeClient({"OSType": "windows"}))

    assert docker_available() is False


def test_unreachable_daemon_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_from_env(monkeypatch, _FakeClient(ping_error=ConnectionError("no daemon")))

    assert docker_available() is False
