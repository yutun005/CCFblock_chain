import pytest

from services.chain_adapter.validation import verify_statistics


def test_deterministic_recalculation_accepts_exact_statistics():
    assert verify_statistics([3, 5, 7, 9], {"count": 4, "sum": 24, "mean": "6"})


@pytest.mark.parametrize(
    "result",
    [
        {"count": 4, "sum": 999, "mean": "6"},
        {"count": 4, "sum": 24, "mean": "7"},
        {"count": 4, "sum": 24, "mean": "NaN"},
        {"count": True, "sum": 24, "mean": "6"},
        {"count": 4, "sum": 24, "mean": "6", "extra": 1},
    ],
)
def test_deterministic_recalculation_rejects_bad_results(result):
    assert not verify_statistics([3, 5, 7, 9], result)
