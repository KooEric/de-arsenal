from pathlib import Path

import pytest

from augur.catalog import Catalog, build_catalog
from augur.fixture import TABLE_NAMES, make


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("data")
    make(d)
    return d


@pytest.fixture(scope="session")
def catalog(data_dir: Path) -> Catalog:
    return build_catalog({n: f"{data_dir}/{n}/*.parquet" for n in TABLE_NAMES})
