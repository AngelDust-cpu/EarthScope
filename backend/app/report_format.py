"""HTTP representation preference shared by the PDF endpoints."""


def wants_pdf(accept: str) -> bool:
    """Keep JSON as the default and require an explicit PDF media range."""
    quality: dict[str, float] = {}
    for part in accept.lower().split(","):
        media, *parameters = part.split(";")
        media = media.strip()
        if media not in {"application/pdf", "application/json", "application/*", "*/*"}:
            continue
        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name.strip() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
                break
        weight = weight if 0 <= weight <= 1 else 0.0
        quality[media] = max(weight, quality.get(media, 0.0))
    pdf_quality = quality.get("application/pdf", 0.0)
    json_quality = quality.get("application/json", quality.get("application/*", quality.get("*/*", 0.0)))
    # Prefer an explicitly requested PDF to an equally weighted wildcard.
    return pdf_quality > 0 and (
        pdf_quality > json_quality or (pdf_quality == json_quality and "application/json" not in quality)
    )
