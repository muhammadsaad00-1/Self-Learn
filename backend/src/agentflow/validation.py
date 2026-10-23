"""Input rules shared by every API module."""

from pydantic import StringConstraints


def non_blank(max_length: int) -> StringConstraints:
    """Free text people type: surrounding whitespace is stripped *before* the length check,
    so "   " is rejected (422) instead of passing validation and failing later. Found in QA:
    a whitespace-only chat message passed `min_length=1`, was stripped to "" and crashed
    while creating its workflow (500).

        name: Annotated[str, non_blank(200)]
    """
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)
