"""Parse one raw email file into a ParsedEmail."""
from __future__ import annotations

from pathlib import Path

from .addresses import parse_address_list
from .attachments import has_attachment
from .body import decode_body, strip_leaked_headers
from .dates import parse_date
from .headers import Headers
from .headings import extract_headings
from .models import ParsedEmail, ParseError, ParseWarning
from .rawfile import split_raw
from .segments import split_body


def _short(value: str | None, limit: int = 120) -> str:
    return repr(value if value is None or len(value) <= limit else value[:limit] + "...")


def parse_bytes(raw: bytes, source_file: str) -> ParsedEmail:
    """Parse raw email bytes. Raises ParseError when a mandatory field can't be extracted."""
    rf = split_raw(raw)
    h = Headers(rf.header_text)
    warnings: list[ParseWarning] = []
    if rf.header_repairs:
        warnings.append(ParseWarning("HEADER_REPAIRED", f"Joined {rf.header_repairs} stray header line(s)"))

    # Collect every mandatory-field problem; the first one is the primary reason.
    problems: list[tuple[str, str]] = []

    message_id = h.get("Message-ID")
    if not message_id:
        problems.append(("MISSING_MESSAGE_ID", "No Message-ID header" if message_id is None else "Empty Message-ID"))

    date_value = h.get("Date")
    date_result = None
    if not date_value:
        problems.append(("MISSING_DATE", "No Date header" if date_value is None else "Empty Date"))
    else:
        try:
            date_result = parse_date(date_value)
            warnings.extend(date_result.warnings)
        except ValueError as exc:
            problems.append(("INVALID_DATE", str(exc)))

    from_value = h.get("From")
    from_list = parse_address_list(from_value)
    if not from_value:
        problems.append(("MISSING_FROM", "No From header" if from_value is None else "Empty From"))
    elif not from_list.valid:
        problems.append(("INVALID_FROM", f"No valid address in From: {_short(from_value)}"))
    elif len(from_list.valid) > 1:
        warnings.append(ParseWarning("MULTIPLE_FROM", f"Using first of {from_list.valid}"))

    to_value = h.get("To")
    to_list = parse_address_list(to_value)
    if to_value is None:
        problems.append(("MISSING_TO", f"No To header (X-To={_short(h.get('X-To'))})"))
    elif not to_list.valid:
        problems.append(("INVALID_TO", f"No valid address in To: {_short(to_value)}"))
    if to_list.rejected and to_list.valid:
        warnings.append(ParseWarning("ADDRESS_REJECTED", f"To items dropped: {to_list.rejected}"))

    subject = h.get_text("Subject")
    if subject is None:
        problems.append(("MISSING_SUBJECT", "No Subject header"))

    if problems:
        code, detail = problems[0]
        if len(problems) > 1:
            detail += " | also: " + ", ".join(c for c, _ in problems[1:])
        raise ParseError(code, detail)

    decoded, body_warnings = decode_body(rf.body, h.get("Content-Transfer-Encoding"), rf.encoding)
    warnings.extend(body_warnings)
    decoded, leaked, leaked_ct = strip_leaked_headers(decoded)
    seg = split_body(decoded, subject)

    cc = parse_address_list(h.get("Cc"))
    bcc = parse_address_list(h.get("Bcc"))
    for name, lst in (("Cc", cc), ("Bcc", bcc)):
        if lst.rejected:
            warnings.append(ParseWarning("ADDRESS_REJECTED", f"{name} items dropped: {lst.rejected}"))

    content_type = h.get_optional("Content-Type")
    media_type = content_type.split(";", 1)[0].strip().lower() or None if content_type else None
    headings = extract_headings("\n\n".join(t for t in (seg.body, seg.forwarded_content) if t))

    return ParsedEmail(
        message_id=message_id,
        date=date_result.utc,
        from_address=from_list.valid[0],
        to_addresses=to_list.valid,
        subject=subject,
        body=seg.body,
        source_file=source_file,
        date_is_sentinel=date_result.is_sentinel,
        cc_addresses=cc.valid,
        bcc_addresses=bcc.valid,
        x_from=h.get_text_optional("X-From"),
        x_to=h.get_text_optional("X-To"),
        x_cc=h.get_text_optional("X-cc"),
        x_bcc=h.get_text_optional("X-bcc"),
        x_folder=h.get_text_optional("X-Folder"),
        x_origin=h.get_text_optional("X-Origin"),
        content_type=media_type,
        has_attachment=has_attachment(content_type, leaked_ct, decoded),
        forwarded_content=seg.forwarded_content,
        quoted_content=seg.quoted_content,
        headings="\n".join(headings) or None,
        split_marker=seg.marker,
        leaked_headers_stripped=leaked,
        warnings=warnings,
    )


def parse_file(path: str | Path, source_file: str) -> ParsedEmail:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ParseError("READ_ERROR", f"{type(exc).__name__}: {exc}") from None
    return parse_bytes(raw, source_file)
