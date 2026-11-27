import pytest

from app.services.director import AIDirector


def test_validate_review_accepts_approval():
    assert AIDirector._validate_review({
        "decision": "approve",
        "feedback": "Отчёт соответствует поручению.",
    }) == {
        "decision": "approve",
        "feedback": "Отчёт соответствует поручению.",
    }


def test_validate_review_requires_feedback_for_revision():
    with pytest.raises(ValueError, match="нужны замечания"):
        AIDirector._validate_review({
            "decision": "request_changes",
            "feedback": "   ",
        })


def test_validate_review_rejects_unknown_decision():
    with pytest.raises(ValueError, match="неизвестное решение"):
        AIDirector._validate_review({
            "decision": "maybe",
            "feedback": "Неопределённый ответ.",
        })


def test_parse_json_response_accepts_fenced_json():
    result = AIDirector._parse_json_response(
        'Ответ:\n```json\n{"decision":"approve","feedback":"Готово"}\n```'
    )
    assert result == {"decision": "approve", "feedback": "Готово"}
