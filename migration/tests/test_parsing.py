import io

import openpyxl
from pptx import Presentation

from app.services.parsing import ParsedSection, chunk_sections, parse_document


def test_excel_parser_preserves_sheet_source():
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["Product", "Revenue"])
    sheet.append(["Alpha", 125000])
    content = io.BytesIO()
    workbook.save(content)

    sections = parse_document("sales.xlsx", content.getvalue())

    assert sections[0].source == "Sheet: Sales"
    assert "Alpha | 125000" in sections[0].text


def test_powerpoint_parser_preserves_slide_source():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Architecture"
    slide.placeholders[1].text = "API, database, worker"
    content = io.BytesIO()
    presentation.save(content)

    sections = parse_document("deck.pptx", content.getvalue())

    assert sections[0].source == "Slide 1"
    assert "Architecture" in sections[0].text


def test_chunk_sections_keeps_short_section_as_single_chunk():
    section = ParsedSection(source="Slide 1", text="A short slide.", metadata={"slide": 1})

    chunks = chunk_sections([section])

    assert chunks == [section]


def test_chunk_sections_splits_long_section_by_tokens_without_crossing_boundaries():
    long_text = "revenue grew significantly this quarter. " * 200
    short_text = "Beta had lower sales."
    sections = [
        ParsedSection(source="Slide 1", text=long_text, metadata={"slide": 1}),
        ParsedSection(source="Slide 2", text=short_text, metadata={"slide": 2}),
    ]

    chunks = chunk_sections(sections, chunk_size_tokens=50, overlap_tokens=10)

    slide_1_chunks = [c for c in chunks if c.source == "Slide 1"]
    slide_2_chunks = [c for c in chunks if c.source == "Slide 2"]

    assert len(slide_1_chunks) > 1  # the long section had to be split
    assert len(slide_2_chunks) == 1  # the short section stayed whole
    assert slide_2_chunks[0].text == short_text  # untouched, not merged with slide 1's tail
    for chunk in slide_1_chunks:
        assert "Beta" not in chunk.text  # never crosses into the next section
        assert "token_offset" in chunk.metadata

