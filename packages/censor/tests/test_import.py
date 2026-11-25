from importlib.metadata import version

import censor


def test_version_matches_distribution_metadata() -> None:
    assert censor.__version__ == version("de-censor")
