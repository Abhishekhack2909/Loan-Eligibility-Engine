"""Deterministic product matching over the supplied CSV fields only."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .schemas import LoanCsvRow, MatchResult


KNOWN_CRITERIA = frozenset(
    {
        "min_monthly_income",
        "max_monthly_income",
        "min_credit_score",
        "max_credit_score",
        "employment_status",
        "min_age",
        "max_age",
    }
)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _compare_number(
    actual: Any, expected: Any, *, minimum: bool
) -> bool | None:
    actual_number = _decimal(actual)
    expected_number = _decimal(expected)
    if actual_number is None or expected_number is None:
        return None
    return actual_number >= expected_number if minimum else actual_number <= expected_number


def _criterion_result(user: LoanCsvRow, name: str, expected: Any) -> bool | None:
    if name == "min_monthly_income":
        return _compare_number(user.monthly_income, expected, minimum=True)
    if name == "max_monthly_income":
        return _compare_number(user.monthly_income, expected, minimum=False)
    if name == "min_credit_score":
        return _compare_number(user.credit_score, expected, minimum=True)
    if name == "max_credit_score":
        return _compare_number(user.credit_score, expected, minimum=False)
    if name == "min_age":
        return _compare_number(user.age, expected, minimum=True)
    if name == "max_age":
        return _compare_number(user.age, expected, minimum=False)
    if name == "employment_status":
        if isinstance(expected, str):
            accepted = {expected.casefold()}
        elif isinstance(expected, (list, tuple, set, frozenset)):
            if not all(isinstance(value, str) for value in expected):
                return None
            accepted = {value.casefold() for value in expected}
        else:
            return None
        if not accepted:
            return None
        return user.employment_status.casefold() in accepted
    return None


def match_user_to_product(
    user: LoanCsvRow, product_criteria: Mapping[str, Any]
) -> MatchResult:
    """Evaluate known criteria and preserve every unknown criterion as unknown.

    Unknown criteria never become an accidental pass or fail. If all known
    rules pass and at least one product rule is unknown, the decision is
    ``unknown`` so a caller cannot represent a partial rule evaluation as an
    approval.
    """

    evaluated: dict[str, bool] = {}
    unknown: dict[str, Any] = {}
    reasons: list[str] = []
    for name in sorted(product_criteria):
        expected = product_criteria[name]
        if name not in KNOWN_CRITERIA:
            unknown[name] = expected
            continue
        result = _criterion_result(user, name, expected)
        if result is None:
            unknown[name] = expected
            continue
        evaluated[name] = result
        if not result:
            reasons.append(f"{name} did not match")

    if any(result is False for result in evaluated.values()):
        decision = "ineligible"
    elif unknown:
        decision = "unknown"
        reasons.append("one or more product criteria are unknown")
    else:
        decision = "eligible"
        reasons.append("all product criteria matched")
    return MatchResult(
        decision=decision,
        evaluated_criteria=evaluated,
        unknown_criteria=unknown,
        reasons=tuple(reasons),
    )
