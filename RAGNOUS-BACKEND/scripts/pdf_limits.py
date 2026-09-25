"""Raise pypdf's decompression caps before loading NCERT scans.

Some NCERT chapters contain very large Flate streams. pypdf caps decompression
output and raises when a stream exceeds it, which surfaces from PyPDFLoader as
"Limit reached while decompressing" and drops the whole chapter.

bulk_ingest.py used to set `pypdf.filters.MAX_BYTES_TO_READ`, an attribute that
does not exist in pypdf >= 6, wrapped in `except Exception: pass`. The guard
silently did nothing and Class 10 Science ch.8 (jesc108, Heredity) never made
it into the index — a hole that is invisible at query time, because a missing
chapter does not error, it just never gets retrieved.

Import and call configure_pdf_limits() before PyPDFLoader in any ingest path.
"""

import pypdf
import pypdf.filters

_LIMIT_NAMES = (
    "ZLIB_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "JBIG2_MAX_OUTPUT_LENGTH",
    "MAX_DECLARED_STREAM_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
)

_LIMIT = 10 ** 10


def configure_pdf_limits() -> list:
    """Raise every decompression cap this pypdf exposes. Returns the names
    raised, and raises RuntimeError if none of them exist — failing loudly is
    the point, since the previous silent version cost a whole chapter."""
    raised = [n for n in _LIMIT_NAMES if hasattr(pypdf.filters, n)]
    for name in raised:
        setattr(pypdf.filters, name, _LIMIT)
    if not raised:
        raise RuntimeError(
            f"None of the pypdf decompression limits {_LIMIT_NAMES} exist in "
            f"pypdf {pypdf.__version__}. Find the current names before "
            f"ingesting, or large chapters will be dropped without an error."
        )
    return raised
