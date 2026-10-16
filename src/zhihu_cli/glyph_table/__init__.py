"""Checked-in glyph reference table for Yanxuan font de-obfuscation.

Zhihu draws the body of paid-column section pages with a per-response subset
font that substitutes high-frequency characters: the codepoints read as garbage
while the glyph outlines draw the real text.  ``glyph_table.bin`` /
``glyph_table.json`` hold a precomputed shape index of the source face
(``Noto Sans CJK SC``), which Zhihu's fonts copy their outlines from, so the
runtime decoder can identify a substituted glyph by rendering it and matching
the result against this table.

The table is generated, not hand-edited — regenerate it with::

    python tools/gen_yanxuan_font_table.py

Importing this package is cheap: the files are only read when
:func:`load_glyph_table` is first called.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_TABLE_DIR = Path(__file__).resolve().parent

_BIN_NAME = "glyph_table.bin"
_JSON_NAME = "glyph_table.json"

#: Bumped whenever the layout of the data files changes.
SUPPORTED_VERSION = 1

#: ``.notdef`` probe used by the decoder: any codepoint the subset font does not
#: cover renders identically to this one.
NOTDEF_PROBE = ""


@dataclass(frozen=True)
class GlyphTable:
    """Shape index of the reference face.

    :ivar render_size: Em size in pixels the glyphs were rendered at.  The
        decoder must render the obfuscated font at the same size.
    :ivar grid: Edge of the square 1-bit raster each glyph was reduced to.
    :ivar characters: Indexed characters, in the order of the rasters.
    :ivar ink_boxes: Ink boxes relative to the glyph origin, one
        ``(x0, y0, x1, y1)`` per character.
    :ivar rasters: Packed 1-bit rasters as integers, one per character.
    :ivar lookalikes: Radical-block look-alikes keyed by character, mapped to
        the unified ideograph they render as.
    """

    render_size: int
    grid: int
    characters: str
    ink_boxes: tuple[tuple[int, int, int, int], ...]
    rasters: tuple[int, ...]
    lookalikes: dict[str, str]

    @property
    def raster_bytes(self) -> int:
        """Number of bytes one packed raster occupies.

        :returns: Byte length of a single raster.
        """
        return ((self.grid + 7) // 8) * self.grid


_table: GlyphTable | None = None


def load_glyph_table() -> GlyphTable:
    """Load (and cache) the packaged glyph reference table.

    :returns: The parsed :class:`GlyphTable`.
    :raises FileNotFoundError: If either data file is missing.
    :raises ValueError: If the files disagree with each other or with
        :data:`SUPPORTED_VERSION`.
    """
    global _table
    if _table is not None:
        return _table

    meta_path = _TABLE_DIR / _JSON_NAME
    bin_path = _TABLE_DIR / _BIN_NAME
    if not meta_path.exists() or not bin_path.exists():
        raise FileNotFoundError(
            f"glyph table data missing in {_TABLE_DIR} — run tools/gen_yanxuan_font_table.py to regenerate it"
        )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    version = meta.get("version")
    if version != SUPPORTED_VERSION:
        raise ValueError(f"unsupported glyph table version {version!r} (expected {SUPPORTED_VERSION})")

    characters = meta["characters"]
    grid = int(meta["grid"])
    if grid % 8:
        raise ValueError(f"glyph table grid must be a multiple of 8, got {grid}")

    blob = bin_path.read_bytes()
    stride = (grid * grid) // 8
    expected = len(characters) * stride
    if len(blob) != expected:
        raise ValueError(f"glyph table raster blob is {len(blob)} bytes, expected {expected}")

    boxes = tuple((int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in meta["ink_boxes"])
    if len(boxes) != len(characters):
        raise ValueError(f"glyph table has {len(boxes)} ink boxes for {len(characters)} characters")

    _table = GlyphTable(
        render_size=int(meta["render_size"]),
        grid=grid,
        characters=characters,
        ink_boxes=boxes,
        rasters=tuple(int.from_bytes(blob[i * stride : (i + 1) * stride], "big") for i in range(len(characters))),
        lookalikes={str(k): str(v) for k, v in meta.get("lookalikes", {}).items()},
    )
    return _table


def pack_raster(bits: bytes, grid: int) -> int:
    """Pack raw 1-bit raster bytes into the integer form used by the table.

    :param bits: Bytes of a mode-``1`` Pillow image, rows padded to whole bytes.
    :param grid: Edge of the square raster.
    :returns: The raster as a big-endian integer.
    """
    stride = (grid * grid) // 8
    if len(bits) != stride:
        raise ValueError(f"raster is {len(bits)} bytes, expected {stride}")
    return int.from_bytes(bits, "big")


def raster_stride(grid: int) -> int:
    """Byte stride of one packed raster row.

    :param grid: Edge of the square raster.
    :returns: Bytes taken by a single row.
    """
    return (grid + 7) // 8


__all__ = [
    "NOTDEF_PROBE",
    "SUPPORTED_VERSION",
    "GlyphTable",
    "load_glyph_table",
    "pack_raster",
    "raster_stride",
]
