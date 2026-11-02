import re
from dataclasses import dataclass

import pymupdf as fitz

from ..config import CHUNK_OVERLAP, CHUNK_SIZE


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    page_number: int | None
    section: str | None
    text: str


def extract_pages(filename: str, content: bytes) -> list[ExtractedPage]:
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension == "pdf":
        try:
            with fitz.open(stream=content, filetype="pdf") as pdf:
                pages = [
                    ExtractedPage(index + 1, page.get_text("text").strip())
                    for index, page in enumerate(pdf)
                ]
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError("The PDF could not be opened or is damaged.") from exc
        if not any(page.text for page in pages):
            raise ValueError("No selectable text was found in the PDF.")
        return pages
    if extension not in {"txt", "md", "markdown"}:
        raise ValueError("Only PDF, TXT, and Markdown files are supported.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Text documents must be encoded as UTF-8.") from exc
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("The document contains no readable text.")
    return [ExtractedPage(1, cleaned)]


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def _section_for(text: str, current: str | None) -> str | None:
    for line in text.splitlines():
        candidate = line.strip().strip("#").strip()
        if candidate and len(candidate) <= 100 and (
            line.lstrip().startswith("#")
            or candidate.isupper()
            or candidate.lower().startswith(
                ("troubleshooting", "maintenance", "warranty", "safety", "installation")
            )
        ):
            return candidate
    return current


def chunk_pages(pages: list[ExtractedPage]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    section: str | None = None
    for page in pages:
        section = _section_for(page.text, section)
        text = page.text.strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind(" ", start, end))
                if boundary > start + CHUNK_SIZE // 2:
                    end = boundary
            fragment = text[start:end].strip()
            if fragment:
                chunks.append(
                    TextChunk(len(chunks), page.page_number, _section_for(fragment, section), fragment)
                )
            if end >= len(text):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks
