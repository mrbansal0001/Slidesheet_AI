from __future__ import annotations

import io
from dataclasses import dataclass

import openpyxl
import tiktoken
from pptx import Presentation

from app.core.config import get_settings


@dataclass
class ParsedSection:
    source: str
    text: str
    metadata: dict


def parse_document(filename: str, content: bytes) -> list[ParsedSection]:
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "pptx":
        return parse_pptx(content)
    if suffix == "xlsx":
        return parse_xlsx(content)
    raise ValueError("Only .pptx and .xlsx files are supported")


def parse_pptx(content: bytes) -> list[ParsedSection]:
    presentation = Presentation(io.BytesIO(content))
    sections: list[ParsedSection] = []

    for slide_number, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        title = slide.shapes.title.text.strip() if slide.shapes.title else ""

        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = "\n".join(
                    paragraph.text.strip()
                    for paragraph in shape.text_frame.paragraphs
                    if paragraph.text.strip()
                )
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    values = [cell.text.strip() for cell in row.cells]
                    if any(values):
                        parts.append(" | ".join(values))
            if getattr(shape, "has_chart", False):
                chart = shape.chart
                for series in chart.series:
                    values = ", ".join(str(value) for value in series.values)
                    parts.append(f"Chart series {series.name}: {values}")

        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"Speaker notes: {notes}")

        unique_parts = list(dict.fromkeys(parts))
        if unique_parts:
            sections.append(
                ParsedSection(
                    source=f"Slide {slide_number}",
                    text="\n".join(unique_parts),
                    metadata={"slide": slide_number, "title": title},
                )
            )
    return sections


def parse_xlsx(content: bytes) -> list[ParsedSection]:
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    sections: list[ParsedSection] = []

    for worksheet in workbook.worksheets:
        rows: list[str] = []
        for row in worksheet.iter_rows(values_only=True):
            values = ["" if value is None else str(value) for value in row]
            if any(values):
                rows.append(" | ".join(values))
        if rows:
            sections.append(
                ParsedSection(
                    source=f"Sheet: {worksheet.title}",
                    text="\n".join(rows),
                    metadata={"sheet": worksheet.title},
                )
            )
    return sections


_encoding_cache: dict[str, tiktoken.Encoding] = {}


def _encoding_for_model(model: str) -> tiktoken.Encoding:
    if model not in _encoding_cache:
        try:
            _encoding_cache[model] = tiktoken.encoding_for_model(model)
        except KeyError:
            # Unknown/future model name — cl100k_base covers ada-002 and the text-embedding-3-*
            # family, a reasonable fallback rather than failing ingestion outright.
            _encoding_cache[model] = tiktoken.get_encoding("cl100k_base")
    return _encoding_cache[model]


def chunk_sections(
    sections: list[ParsedSection],
    chunk_size_tokens: int = 300,
    overlap_tokens: int = 40,
) -> list[ParsedSection]:
    """Split each section into token-sized windows, never crossing a section boundary.

    Sized in tokens (not characters) because that's what the embedding model's context limit and
    per-request token budget actually count in — a fixed character window is only a rough proxy.
    """
    encoding = _encoding_for_model(get_settings().openai_embedding_model)
    chunks: list[ParsedSection] = []
    step = chunk_size_tokens - overlap_tokens
    for section in sections:
        token_ids = encoding.encode(section.text)
        if len(token_ids) <= chunk_size_tokens:
            chunks.append(section)
            continue
        for start in range(0, len(token_ids), step):
            text = encoding.decode(token_ids[start : start + chunk_size_tokens]).strip()
            if text:
                chunks.append(
                    ParsedSection(
                        source=section.source,
                        text=text,
                        metadata={**section.metadata, "token_offset": start},
                    )
                )
    return chunks

