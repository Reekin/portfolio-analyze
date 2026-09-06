"""Date-weighted investment returns."""

import math
from collections import defaultdict
from datetime import date


def xirr(cashflows: list[tuple[date, float]]) -> float | None:
    """Annual rate with Actual/365 dates; ambiguous or unsolved roots return None."""
    grouped = defaultdict(float)
    for day, amount in cashflows:
        grouped[day] += amount
    flows = sorted((day, value) for day, value in grouped.items() if value)
    if len(flows) < 2 or not any(v < 0 for _, v in flows) or not any(v > 0 for _, v in flows):
        return None
    origin = flows[0][0]
    terms = [((day - origin).days / 365, value) for day, value in flows]

    def npv(log_rate):
        shift = max(-log_rate * years for years, _ in terms)
        return math.fsum(value * math.exp(-log_rate * years - shift) for years, value in terms)

    # Search log(1 + rate) so negative rates remain strictly above -100%.
    roots = []
    left, left_value = -20.0, npv(-20.0)
    for step in range(1, 401):
        right = -20 + step / 10
        right_value = npv(right)
        if right_value == 0:
            roots.append(right)
        elif left_value * right_value < 0:
            low, high, low_value = left, right, left_value
            for _ in range(80):
                middle = (low + high) / 2
                value = npv(middle)
                if value == 0:
                    low = high = middle
                    break
                if low_value * value < 0:
                    high = middle
                else:
                    low, low_value = middle, value
            roots.append((low + high) / 2)
        left, left_value = right, right_value
    return math.expm1(roots[0]) if len(roots) == 1 else None
