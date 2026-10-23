"""Reply language from the customer's own message (code, not the model)."""

import pytest

from agentflow.agents.language import detect


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Lahore se Islamabad kal subah ki bus late hai kya?", "roman_ur"),  # real-run miss
        ("Maine Ali ko 5000 bheje, kat gaye lekin usko nahi mile", "roman_ur"),
        ("میرے پیسے کٹ گئے", "ur"),
        ("Is there a seat to Lahore tomorrow evening?", None),
        ("I sent money to Ali Khan", None),  # names and "to" aren't Urdu
        ("thanks bhai", None),  # one word: not enough to switch
    ],
)
def test_language_from_the_customers_message(text: str, expected: str | None) -> None:
    assert detect(text) == expected
