"""Query-focused passage selection: split content into chunks and rank them with BM25."""

import math
import re
from collections import Counter

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i in is it of on or "
    "that the this to was were what when where which who why will with you".split()
)


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(text.lower()) if token not in STOPWORDS]


def _split_long(text: str, target_chars: int) -> list[str]:
    """Break an oversized line at sentence ends, then hard-wrap whatever is still too long."""
    if len(text) <= target_chars * 2:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) > target_chars:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}" if current else sentence
    if current:
        pieces.append(current)
    return [piece[i:i + target_chars * 2] for piece in pieces for i in range(0, len(piece), target_chars * 2)]


def chunk(content: str, target_chars: int = 800) -> list[str]:
    """Split on blank lines, merge short blocks, and start a new chunk at each heading."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", content):
        block = block.strip()
        if len(block) > target_chars * 2:
            for line in block.splitlines():
                if line.strip():
                    blocks.extend(_split_long(line.strip(), target_chars))
        elif block:
            blocks.append(block)

    chunks: list[str] = []
    current = ""
    for block in blocks:
        new_section = block.startswith("#") and len(current) > target_chars // 4
        if current and (new_section or len(current) + len(block) > target_chars):
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


def top_passages(content: str, query: str, k: int, k1: float = 1.5, b: float = 0.75) -> list[str]:
    """Return up to k chunks most relevant to query, in their original document order."""
    chunks = chunk(content)
    terms = set(_tokens(query))
    if not terms or not chunks:
        return []

    docs = [Counter(_tokens(text)) for text in chunks]
    lengths = [sum(doc.values()) for doc in docs]
    avg_length = (sum(lengths) / len(docs)) or 1
    doc_freq = Counter(term for doc in docs for term in terms if term in doc)

    scored = []
    for index, doc in enumerate(docs):
        score = 0.0
        for term in terms:
            freq = doc.get(term, 0)
            if freq:
                idf = math.log(1 + (len(docs) - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                score += idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * lengths[index] / avg_length))
        if score > 0:
            scored.append((score, index))

    best = sorted(scored, reverse=True)[:k]
    return [chunks[index] for _, index in sorted(best, key=lambda item: item[1])]
