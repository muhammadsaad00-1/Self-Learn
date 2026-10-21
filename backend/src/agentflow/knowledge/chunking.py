"""Split a document into chunks for retrieval.

Design choices (each one is a retrieval-quality lever, tuned later with the RAG evals):

- Heading-aware: markdown headings set the chunk's `section` ("Card policy > Lost cards"),
  and a chunk never spans two sections. One chunk is then about one topic, and its
  citation can point at a heading a human can find.
- Heading as context: `embed_text` prefixes the section path, so a chunk that only says
  "it is reversed within 3 days" still carries "Duplicate charges" in its vector. (The
  keyword index gets the heading through the `search` column instead.)
- Size by words, packed from whole sentences: ~180 words keeps a chunk focused and far
  below the embedding model's 512-token limit (text beyond it would be silently cut off).
- Overlap: the next chunk repeats the previous one's last ~30 words, so a fact that falls
  on a chunk boundary is still complete in at least one chunk.
"""

import re
from dataclasses import dataclass

MAX_WORDS = 180
OVERLAP_WORDS = 30
MAX_SECTION_CHARS = 300  # matches knowledge_chunks.section

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    section: str | None
    content: str

    @property
    def embed_text(self) -> str:
        """What gets embedded: the section path gives the chunk its topic."""
        return f"{self.section}\n{self.content}" if self.section else self.content


def _sections(text: str) -> list[tuple[str | None, list[str]]]:
    """[(section path, paragraphs)] in document order. Headings nest: an h3 under an h2
    gets the path "H2 > H3"; a new h2 resets everything below it."""
    path: list[tuple[int, str]] = []  # (level, title) stack
    out: list[tuple[str | None, list[str]]] = []
    paragraph: list[str] = []
    paragraphs: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            paragraphs.append(" ".join(paragraph))
            paragraph.clear()

    def close_section() -> None:
        close_paragraph()
        if paragraphs:
            section = " > ".join(title for _, title in path) or None
            out.append((section[:MAX_SECTION_CHARS] if section else None, paragraphs.copy()))
            paragraphs.clear()

    for line in text.splitlines():
        if match := _HEADING.match(line.strip()):
            close_section()
            level = len(match.group(1))
            path = [(lvl, title) for lvl, title in path if lvl < level]
            path.append((level, match.group(2)))
        elif line.strip():
            paragraph.append(line.strip())
        else:
            close_paragraph()
    close_section()
    return out


def _units(paragraphs: list[str], max_words: int) -> list[tuple[int, str]]:
    """Sentences tagged with their paragraph index. A sentence longer than max_words (a
    table row, a run-on clause) is cut into max_words pieces so no unit exceeds a chunk."""
    units = []
    for p, paragraph in enumerate(paragraphs):
        for sentence in _SENTENCE_END.split(paragraph):
            words = sentence.split()
            for start in range(0, len(words), max_words):
                units.append((p, " ".join(words[start : start + max_words])))
    return units


def _join(units: list[tuple[int, str]]) -> str:
    """Sentences of one paragraph join with a space; a new paragraph starts a new block."""
    text = ""
    for i, (p, sentence) in enumerate(units):
        if i:
            text += "\n\n" if p != units[i - 1][0] else " "
        text += sentence
    return text


def chunk_text(
    text: str, *, max_words: int = MAX_WORDS, overlap_words: int = OVERLAP_WORDS
) -> list[ChunkDraft]:
    """Chunks of at most `max_words` words, in document order, numbered from 0."""
    if overlap_words >= max_words:
        raise ValueError("overlap_words must be smaller than max_words")
    drafts: list[ChunkDraft] = []
    for section, paragraphs in _sections(text):
        units = _units(paragraphs, max_words)
        window: list[tuple[int, str]] = []
        words = 0
        for unit in units:
            size = len(unit[1].split())
            if window and words + size > max_words:
                drafts.append(ChunkDraft(len(drafts), section, _join(window)))
                # Carry the trailing sentences (up to overlap_words) into the next chunk.
                carried: list[tuple[int, str]] = []
                carried_words = 0
                for prev in reversed(window):
                    n = len(prev[1].split())
                    if carried_words + n > overlap_words or carried_words + n + size > max_words:
                        break
                    carried.insert(0, prev)
                    carried_words += n
                window, words = carried, carried_words
            window.append(unit)
            words += size
        if window:
            drafts.append(ChunkDraft(len(drafts), section, _join(window)))
    return drafts
