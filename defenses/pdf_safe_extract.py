"""
Layer 5.2.2 — Document-level sanitization (safe PDF extraction).

Extracts text from a PDF while skipping:
    1) White text on a white background (luminance contrast below threshold)
    2) Microprint below a defined minimum size
    3) PDF metadata (Author, Subject, Keywords, Title)
    4) Text outside the page's visible area (MediaBox)

This is the single most effective defense against indirect data-based
attacks that rely on the attacker's hidden content reaching the model's
input via naive text extraction.

Requires: PyMuPDF (fitz)
"""

from dataclasses import dataclass, field


@dataclass
class ExtractionReport:
    """Diagnostic information from the extraction process."""
    text: str = ""
    blocks_total: int = 0
    blocks_kept: int = 0
    blocks_filtered_color: int = 0
    blocks_filtered_size: int = 0
    blocks_filtered_outside: int = 0
    metadata_dropped: bool = False
    filtered_excerpts: list[str] = field(default_factory=list)  # samples of filtered-out content

    def summary(self) -> str:
        return (
            f"Extraction: {self.blocks_kept}/{self.blocks_total} blocks kept. "
            f"Filtered: color={self.blocks_filtered_color}, "
            f"size={self.blocks_filtered_size}, "
            f"outside={self.blocks_filtered_outside}, "
            f"metadata={'yes' if self.metadata_dropped else 'no'}."
        )


def _luminance_rec709(rgb_int: int) -> float:
    """Compute the relative luminance of a color per Rec. 709.
    Input: 24-bit RGB integer as returned by PyMuPDF (e.g. 0xFFFFFF = white)."""
    r = ((rgb_int >> 16) & 0xFF) / 255.0
    g = ((rgb_int >> 8) & 0xFF) / 255.0
    b = (rgb_int & 0xFF) / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def safe_extract_pdf(
    pdf_path: str,
    min_font_size: float = 4.0,
    min_contrast: float = 0.05,
    background_luminance: float = 1.0,
    include_metadata: bool = False,
    keep_filtered_examples: int = 5,
) -> ExtractionReport:
    """
    Safe extraction of text from a PDF.

    Args:
        pdf_path: path to the PDF file.
        min_font_size: minimum font size in points (text below this size
            is filtered out). Default 4.0 pt — below this threshold text
            is not meaningfully readable in normal documents.
        min_contrast: minimum luminance difference between text and background.
            Default 0.05 — filters out text with lower luminance difference
            (typically white-on-white, or very light gray on white).
        background_luminance: assumed background luminance (1.0 = white).
            In practice always white for invoices and resumes.
        include_metadata: if True, includes PDF metadata (Author, Subject…).
            Default False — metadata does not enter the extracted text.
        keep_filtered_examples: how many samples of filtered text to keep
            in the report for diagnostics.

    Returns:
        ExtractionReport with sanitized text and diagnostics.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ImportError(
            "safe_extract_pdf requires PyMuPDF. Install: pip install pymupdf"
        ) from e

    report = ExtractionReport()
    doc = fitz.open(pdf_path)
    output_pieces: list[str] = []

    for page in doc:
        media = page.mediabox  # visible page area
        try:
            page_dict = page.get_text("dict")
        except Exception:
            # Fallback if the page is corrupted
            continue

        for block in page_dict.get("blocks", []):
            if block.get("type", 0) != 0:
                # Type 0 = text block; 1 = image
                continue

            block_lines: list[str] = []
            for line in block.get("lines", []):
                line_pieces: list[str] = []
                for span in line.get("spans", []):
                    report.blocks_total += 1
                    text = span.get("text", "")
                    if not text.strip():
                        continue

                    size = span.get("size", 12.0)
                    color = span.get("color", 0)  # 24-bit int, default black
                    bbox = span.get("bbox", (0, 0, 0, 0))

                    # 1) Font size
                    if size < min_font_size:
                        report.blocks_filtered_size += 1
                        if len(report.filtered_excerpts) < keep_filtered_examples:
                            report.filtered_excerpts.append(
                                f"[font={size:.1f}pt] {text[:80]}"
                            )
                        continue

                    # 2) Color / contrast
                    text_lum = _luminance_rec709(color)
                    contrast = abs(text_lum - background_luminance)
                    if contrast < min_contrast:
                        report.blocks_filtered_color += 1
                        if len(report.filtered_excerpts) < keep_filtered_examples:
                            report.filtered_excerpts.append(
                                f"[color=#{color:06X}, contrast={contrast:.3f}] {text[:80]}"
                            )
                        continue

                    # 3) Position outside visible area
                    x0, y0, x1, y1 = bbox
                    if x1 < media.x0 or x0 > media.x1 or y1 < media.y0 or y0 > media.y1:
                        report.blocks_filtered_outside += 1
                        if len(report.filtered_excerpts) < keep_filtered_examples:
                            report.filtered_excerpts.append(
                                f"[outside_page] {text[:80]}"
                            )
                        continue

                    # Block passed all checks
                    line_pieces.append(text)
                    report.blocks_kept += 1

                if line_pieces:
                    block_lines.append("".join(line_pieces))

            if block_lines:
                output_pieces.append("\n".join(block_lines))

    doc.close()

    # Metadata — either include or drop (default: drop)
    if include_metadata:
        try:
            doc = fitz.open(pdf_path)
            meta = doc.metadata or {}
            doc.close()
            for key in ("author", "subject", "keywords", "title"):
                val = meta.get(key, "")
                if val:
                    output_pieces.append(f"\n[Metadata {key}]: {val}")
        except Exception:
            pass
    else:
        report.metadata_dropped = True

    report.text = "\n\n".join(output_pieces)
    return report


def safe_extract_text(pdf_path: str, **kwargs) -> str:
    """Convenience wrapper — returns only the sanitized text without the report."""
    return safe_extract_pdf(pdf_path, **kwargs).text
