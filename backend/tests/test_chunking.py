"""Chunker: sections from headings, size limits, overlap, edge cases."""

from itertools import pairwise

import pytest

from agentflow.knowledge.chunking import ChunkDraft, chunk_text

POLICY = """\
# Customer policies

## Duplicate charges
If the same payment is debited more than once, the duplicate is reversed in full.

It takes 3 business days. No fee applies.

## Cards
### Lost or stolen cards
Report a lost card in the app. It is blocked within 1 hour.
"""


def words(chunk: ChunkDraft) -> int:
    return len(chunk.content.split())


def test_headings_become_nested_section_paths() -> None:
    chunks = chunk_text(POLICY)
    assert [c.section for c in chunks] == [
        "Customer policies > Duplicate charges",
        "Customer policies > Cards > Lost or stolen cards",  # h3 nests under its h2
    ]
    assert [c.ordinal for c in chunks] == [0, 1]


def test_paragraph_breaks_survive_inside_a_chunk() -> None:
    first = chunk_text(POLICY)[0]
    assert first.content == (
        "If the same payment is debited more than once, the duplicate is reversed in full."
        "\n\nIt takes 3 business days. No fee applies."
    )


def test_embed_text_carries_the_heading() -> None:
    first = chunk_text(POLICY)[0]
    assert first.embed_text.startswith("Customer policies > Duplicate charges\nIf the same")


def test_a_chunk_never_spans_two_sections() -> None:
    text = "## A\n" + "Alpha fact. " * 5 + "\n## B\n" + "Beta fact. " * 5
    chunks = chunk_text(text, max_words=100)
    assert [(c.section, "Beta" in c.content) for c in chunks] == [("A", False), ("B", True)]


def test_long_sections_split_within_the_limit_with_overlap() -> None:
    sentences = [f"Sentence number {i} has exactly six words." for i in range(40)]
    chunks = chunk_text("## Long\n" + " ".join(sentences), max_words=60, overlap_words=12)
    assert len(chunks) > 3
    assert all(words(c) <= 60 for c in chunks)
    for prev, nxt in pairwise(chunks):
        # The next chunk starts by repeating the previous chunk's last sentences.
        assert nxt.content.split(". ")[0] in prev.content
    # Nothing lost: every sentence appears in some chunk.
    assert all(any(s.rstrip(".") in c.content for c in chunks) for s in sentences)


def test_a_sentence_longer_than_a_chunk_is_cut() -> None:
    chunks = chunk_text("## Table\n" + " ".join(f"cell{i}" for i in range(250)), max_words=100)
    assert [words(c) for c in chunks] == [100, 100, 50]


def test_text_without_headings_has_no_section() -> None:
    chunks = chunk_text("Plain text.\n\nSecond paragraph.")
    assert [(c.section, c.embed_text) for c in chunks] == [
        (None, "Plain text.\n\nSecond paragraph.")
    ]


@pytest.mark.parametrize("text", ["", "   \n\n  ", "# Only a heading\n## And another"])
def test_nothing_to_chunk(text: str) -> None:
    assert chunk_text(text) == []


def test_overlap_must_be_smaller_than_chunk() -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("text", max_words=10, overlap_words=10)
