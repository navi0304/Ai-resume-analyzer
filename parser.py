from __future__ import annotations

import io

import PyPDF2


def extract_text(file) -> str:
    """Extract readable text from an uploaded resume file."""
    filename = getattr(file, "filename", "") or ""

    # Simple fallback for plain-text uploads.
    if filename.lower().endswith(".txt"):
        data = file.read()
        if hasattr(file, "seek"):
            file.seek(0)
        return data.decode("utf-8", errors="ignore").strip()

    try:
        stream = getattr(file, "stream", file)

        if hasattr(stream, "seek"):
            stream.seek(0)

        # Some upload wrappers need a BytesIO copy for reliable reads.
        buffer = io.BytesIO(stream.read())
        reader = PyPDF2.PdfReader(buffer)

        chunks = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            chunks.append(page_text)

        if hasattr(stream, "seek"):
            stream.seek(0)

        return "\n".join(chunks).strip()
    except Exception:
        return ""
