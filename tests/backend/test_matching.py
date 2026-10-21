from decimal import Decimal

from backend.app.matching import match_user_to_product
from backend.app.schemas import LoanCsvRow


def user() -> LoanCsvRow:
    return LoanCsvRow(
        user_id="u-1",
        name="Ada",
        email="ada@example.com",
        monthly_income=Decimal("5000"),
        credit_score=720,
        employment_status="employed",
        age=30,
    )


def test_matching_is_eligible_when_all_known_criteria_match() -> None:
    result = match_user_to_product(
        user(),
        {
            "min_monthly_income": 4000,
            "min_credit_score": 700,
            "employment_status": ["employed"],
            "min_age": 21,
            "max_age": 65,
        },
    )

    assert result.decision == "eligible"
    assert result.unknown_criteria == {}
    assert all(result.evaluated_criteria.values())


def test_unknown_product_criteria_never_becomes_an_approval() -> None:
    result = match_user_to_product(
        user(),
        {"min_credit_score": 700, "debt_to_income_ratio": 0.4},
    )

    assert result.decision == "unknown"
    assert result.unknown_criteria == {"debt_to_income_ratio": 0.4}
    assert result.evaluated_criteria == {"min_credit_score": True}


def test_known_failed_criterion_wins_over_unknown_criterion() -> None:
    result = match_user_to_product(
        user(),
        {"min_credit_score": 800, "property_value": 200000},
    )

    assert result.decision == "ineligible"
    assert result.unknown_criteria == {"property_value": 200000}
