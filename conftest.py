"""Repo-root conftest — shared fixtures for `packages/` and `tests/`.

pytest loads conftest.py files from the rootdir down to each collected test
module's directory, so this file applies to every test in the workspace
without any explicit import (see pyproject.toml `testpaths`).

Docker-dependent tests (testcontainers Postgres/MySQL) must SKIP — not
ERROR — when the Docker daemon is unreachable. GitHub's macOS and Windows
runners have no usable Linux Docker daemon; `testcontainers[postgres]` is
installed there as a dev dependency (so `pytest.importorskip` alone doesn't
help), and starting a container raises a raw `requests.exceptions
.ConnectionError` / `FileNotFoundError` instead of a pytest skip. The
`require_docker` fixture below turns that into a clean skip at fixture
setup time, before any container is started.
"""

import pytest


def docker_available() -> bool:
    """Check Docker daemon reachability without raising.

    Returns False for any failure mode (daemon absent, socket missing,
    permission denied, etc.) rather than letting the caller crash.
    """
    try:
        import docker

        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def require_docker() -> None:
    """Skip the requesting test/fixture if the Docker daemon is unreachable.

    Depend on this fixture from any container-starting fixture (module or
    function scoped — a lower-scoped fixture may depend on a session-scoped
    one) so the skip happens at setup, before `.start()` is ever called.
    """
    if not docker_available():
        pytest.skip("Docker daemon not available")
