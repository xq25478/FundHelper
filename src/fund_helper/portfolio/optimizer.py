"""Placeholder for future cvxpy-based optimizer (min-variance / risk-parity)."""
from __future__ import annotations


def equal_weight(codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    w = 1.0 / len(codes)
    return {c: w for c in codes}
