"""JPEG byte-level normalization helpers.

Tencent's QQ rich-media upload (NTQQ highway) rejects "naked" JPEGs — files
whose SOI marker (FF D8) is immediately followed by a DQT/SOF segment with no
APPn identifier segment — with ``rich media transfer failed`` (retcode 1200).

This happens because ``pyvips``/libjpeg ``jpegsave(strip=True)`` strips *all*
metadata, including the standard JFIF APP0 segment, producing ``FF D8 FF DB...``
instead of the conventional ``FF D8 FF E0 ... JFIF``. The image is still a valid
JPEG and decodes fine locally, but QQ refuses to relay it.

``ensure_jfif_app0`` re-inserts a minimal standard JFIF APP0 segment (18 bytes,
no thumbnail) right after the SOI when one is absent, without touching the image
data — turning a naked JPEG back into a QQ-acceptable ``FF D8 FF E0`` file.
"""

from __future__ import annotations

import struct

_SOI = b"\xff\xd8"

# Minimal, standard JFIF APP0 segment (no thumbnail):
#   FF E0            APP0 marker
#   00 10            length = 16
#   4A 46 49 46 00   "JFIF\0"
#   01 01            version 1.01
#   00               density units = 0 (aspect ratio only)
#   00 01 00 01      X density 1, Y density 1
#   00 00            thumbnail 0x0
_JFIF_APP0 = (
    b"\xff\xe0"
    + struct.pack(">H", 16)
    + b"JFIF\x00"
    + b"\x01\x01"
    + b"\x00"
    + struct.pack(">HH", 1, 1)
    + b"\x00\x00"
)


def is_naked_jpeg(data: bytes) -> bool:
    """True if ``data`` is a JPEG whose SOI is not followed by an APPn segment.

    APPn markers are ``FF E0``–``FF EF``. A JFIF (``FF E0``) or EXIF (``FF E1``)
    header is what QQ expects; anything else after SOI (e.g. ``FF DB`` DQT) is a
    naked JPEG that QQ's rich-media upload rejects.
    """
    if data[:3] != b"\xff\xd8\xff":
        return False
    marker = data[3]
    return not (0xE0 <= marker <= 0xEF)


def ensure_jfif_app0(data: bytes) -> bytes:
    """Return ``data`` with a standard JFIF APP0 segment after the SOI.

    No-op (returns the input unchanged) for non-JPEG data or a JPEG that already
    carries an APPn segment. For a naked JPEG, inserts ``_JFIF_APP0`` right after
    the 2-byte SOI, leaving all image data untouched.
    """
    if not is_naked_jpeg(data):
        return data
    return data[:2] + _JFIF_APP0 + data[2:]
