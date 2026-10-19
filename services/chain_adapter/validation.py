"""Small, objective demonstration rule; module A can replace it with a versioned policy."""

from decimal import Decimal, InvalidOperation, localcontext


def verify_statistics(values: list[int], result: dict) -> bool:
    if not values or not all(type(v) is int for v in values):
        return False
    if set(result) != {"count", "sum", "mean"}:
        return False
    if type(result["count"]) is not int or type(result["sum"]) is not int:
        return False
    if result["count"] != len(values) or result["sum"] != sum(values):
        return False
    if not isinstance(result["mean"], str):
        return False
    try:
        with localcontext() as ctx:
            ctx.prec = 100
            mean = Decimal(result["mean"])
            return mean.is_finite() and mean * len(values) == sum(values)
    except InvalidOperation:
        return False
