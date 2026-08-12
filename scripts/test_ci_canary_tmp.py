"""Temporary canary: proves CI fails when a scripts/test_*.py file fails.
Removed in the same pull request once the red run is recorded."""


def test_canary_must_fail():
    assert False, "canary: CI must report this failure"


if __name__ == "__main__":
    test_canary_must_fail()
