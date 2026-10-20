"""Reference glyph index and reference-table generator for Yanxuan decoding.

Zhihu draws the body of paid-column section pages with a per-response subset
font whose cipher mapping rotates.  Measured behaviour: there are distinct font
generations, and the same source codepoint maps to different characters in
different generations, so a static cipher table is impossible.

This package is therefore **not** a cipher table: it is a static shape index of
the reference face (``Noto Sans CJK SC`` / ``Source Han Sans CN``) used by the
runtime matcher.  The index describes glyph shapes only and does not depend on
which cipher generation produced a response, so it is correctly static.

The index is generated, not hand-edited — regenerate it with::

    python -m zhihu_cli.font_reference

That requires Pillow and the reference face; see ``--font``.  Importing the
package stays cheap: ``font_reference.bin`` / ``font_reference.json`` are read
only when :func:`load_font_reference` is first called, and generator-only
imports (``argparse``, ``gzip``, ``sys``, ``unicodedata``, PIL) live inside the
generator functions that use them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageFont

_TABLE_DIR = Path(__file__).resolve().parent

_BIN_NAME = "font_reference.bin"
_JSON_NAME = "font_reference.json"

#: Bumped whenever the layout of the data files changes.
SUPPORTED_VERSION = 1

#: ``.notdef`` probe used by the decoder: any codepoint the subset font does not
#: cover renders identically to this one.
NOTDEF_PROBE = ""


@dataclass(frozen=True)
class FontReference:
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


_reference: FontReference | None = None


def load_font_reference() -> FontReference:
    """Load (and cache) the packaged reference glyph index.

    :returns: The parsed :class:`FontReference`.
    :raises FileNotFoundError: If either data file is missing.
    :raises ValueError: If the files disagree with each other or with
        :data:`SUPPORTED_VERSION`.
    """
    global _reference
    if _reference is not None:
        return _reference

    meta_path = _TABLE_DIR / _JSON_NAME
    bin_path = _TABLE_DIR / _BIN_NAME
    if not meta_path.exists() or not bin_path.exists():
        raise FileNotFoundError(
            f"font reference data missing in {_TABLE_DIR} — run `python -m zhihu_cli.font_reference` to regenerate it"
        )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    version = meta.get("version")
    if version != SUPPORTED_VERSION:
        raise ValueError(f"unsupported font reference version {version!r} (expected {SUPPORTED_VERSION})")

    characters = meta["characters"]
    grid = int(meta["grid"])
    if grid % 8:
        raise ValueError(f"font reference grid must be a multiple of 8, got {grid}")

    blob = bin_path.read_bytes()
    stride = (grid * grid) // 8
    expected = len(characters) * stride
    if len(blob) != expected:
        raise ValueError(f"font reference raster blob is {len(blob)} bytes, expected {expected}")

    boxes = tuple((int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in meta["ink_boxes"])
    if len(boxes) != len(characters):
        raise ValueError(f"font reference has {len(boxes)} ink boxes for {len(characters)} characters")

    _reference = FontReference(
        render_size=int(meta["render_size"]),
        grid=grid,
        characters=characters,
        ink_boxes=boxes,
        rasters=tuple(int.from_bytes(blob[i * stride : (i + 1) * stride], "big") for i in range(len(characters))),
        lookalikes={str(k): str(v) for k, v in meta.get("lookalikes", {}).items()},
    )
    return _reference


def pack_raster(bits: bytes, grid: int) -> int:
    """Pack raw 1-bit raster bytes into the integer form used by the index.

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


#: Default reference face: ``Noto Sans CJK SC`` is face index 2 of the TTC
#: (the TTC also carries the JP/KR/HK/TC faces).
_DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_DEFAULT_FACE = 2

#: Em size in pixels used to render every glyph.  The runtime decoder renders
#: the obfuscated font at the same size, so the two must agree.
_RENDER_SIZE = 96

#: Edge of the square raster every glyph is normalised to.
_GRID = 32

#: Unicode blocks whose characters are stored as look-alikes of ordinary
#: ideographs (e.g. ``U+2F08`` KANGXI RADICAL MAN instead of ``U+4EBA`` 人).
_LOOKALIKE_BLOCKS = (
    (0x2E80, 0x2EFF),
    (0x2F00, 0x2FDF),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)

#: A look-alike is only resolved automatically when its raster is close to the
#: best-matching ideograph.  Radicals whose match is further away than this are
#: left alone: guessing would silently corrupt the text.
_LOOKALIKE_MAX_DISTANCE = 64

#: Characters added on top of GB2312 level 1 — ASCII, CJK punctuation, the
#: fullwidth forms and the handful of general-punctuation marks that occur in
#: Chinese prose.
_EXTRA_CHARACTERS = (
    "".join(chr(cp) for cp in range(0x21, 0x7F))
    + "".join(chr(cp) for cp in range(0x3000, 0x3040))
    + "".join(chr(cp) for cp in range(0xFF01, 0xFF5F))
    + "—…‘’“”·〈〉〖〗～￥°±×÷≈≤≥№→←↑↓①②③④⑤⑥⑦⑧⑨⑩"
)


def _gb2312_level1() -> list[str]:
    """Return the 3755 characters of GB2312 level 1.

    Level 1 is the common-character half of GB2312 (rows ``0xB0``–``0xD7``) and
    covers virtually all running Chinese text — which is the set Zhihu draws
    substitutes from, since only high-frequency characters are obfuscated.

    :returns: List of characters in code order.
    """
    out: list[str] = []
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                out.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return out


def _character_set() -> list[str]:
    """Build the reference charset, de-duplicated and order-stable.

    :returns: List of characters to index.
    """
    return list(dict.fromkeys(_gb2312_level1() + list(_EXTRA_CHARACTERS)))


def _glyph_feature(font: ImageFont.FreeTypeFont, char: str, size: int = _RENDER_SIZE, grid: int = _GRID):
    """Render *char* and reduce it to ``(ink_box, raster)``.

    The glyph is drawn at a fixed baseline origin, so the ink box comes back
    relative to that origin — the same coordinates the obfuscated font produces,
    because it copies the source outlines unit for unit.

    :param font: Loaded reference face.
    :param char: Character to render.
    :param size: Em size in pixels.
    :param grid: Edge of the square raster.
    :returns: ``(x0, y0, x1, y1, raster_bytes)``, or ``None`` when the glyph
        has no ink (e.g. a space) or would be clipped by the drawing canvas —
        the runtime decoder refuses the same cases.
    """
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(canvas).text((size, size), char, font=font, fill=255, anchor="ls")
    box = canvas.getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    edge = size * 3
    if x0 <= 0 or y0 <= 0 or x1 >= edge or y1 >= edge:
        return None
    small = canvas.crop(box).resize((grid, grid), Image.BILINEAR).convert("1", dither=Image.Dither.NONE)
    return (x0 - size, y0 - size, x1 - size, y1 - size, small.tobytes())


def _raster_to_int(raster: bytes) -> int:
    """Pack a 1-bit raster into an integer for fast Hamming comparison.

    :param raster: Bytes from :func:`_glyph_feature`.
    :returns: The raster as a big-endian integer.
    """
    return int.from_bytes(raster, "big")


def _build_lookalike_map(
    font: ImageFont.FreeTypeFont,
    entries: list[tuple[str, tuple[int, int, int, int, bytes]]],
) -> dict[str, str]:
    """Resolve radical-block look-alikes to the unified ideograph they render.

    Characters outside the unified blocks whose ``NFKC`` normalisation is a
    no-op (most of the CJK Radicals Supplement) are matched against the
    reference rasters; a match is only kept when it is close enough to be
    trustworthy.  Unassigned codepoints, which render as a placeholder box, are
    skipped.

    :param font: Loaded reference face.
    :param entries: ``(char, feature)`` pairs in index order.
    :returns: Mapping of look-alike character to unified ideograph.
    """
    import unicodedata

    rasters = [(char, _raster_to_int(feature[4])) for char, feature in entries]
    resolved: dict[str, str] = {}
    for start, end in _LOOKALIKE_BLOCKS:
        for cp in range(start, end + 1):
            char = chr(cp)
            if unicodedata.normalize("NFKC", char) != char:
                # NFKC already maps it; the runtime normalisation handles it.
                continue
            if unicodedata.name(char, None) is None:
                # Unassigned codepoint — renders as a placeholder, matches nothing.
                continue
            feature = _glyph_feature(font, char)
            if feature is None:
                continue
            probe = _raster_to_int(feature[4])
            best_char, best_distance = "", 1 << 30
            for candidate, candidate_raster in rasters:
                distance = (probe ^ candidate_raster).bit_count()
                if distance < best_distance:
                    best_char, best_distance = candidate, distance
            if best_char and best_distance <= _LOOKALIKE_MAX_DISTANCE:
                resolved[char] = best_char
                print(f"  look-alike U+{cp:04X} {char} -> {best_char}  (distance {best_distance})")
    return resolved


def _write_table(out_dir: Path, font: ImageFont.FreeTypeFont) -> None:
    """Render the charset and write the index files into *out_dir*.

    :param out_dir: Directory receiving ``font_reference.bin`` /
        ``font_reference.json``.
    :param font: Loaded reference face.
    """
    import gzip

    entries: list[tuple[str, tuple[int, int, int, int, bytes]]] = []
    skipped: list[str] = []
    for char in _character_set():
        feature = _glyph_feature(font, char)
        if feature is None:
            skipped.append(char)
            continue
        entries.append((char, feature))
    if skipped:
        print(f"skipped {len(skipped)} characters with no ink or a clipped glyph: {''.join(skipped)}")

    lookalikes = _build_lookalike_map(font, entries)

    blob = bytearray()
    for char, feature in entries:
        blob += feature[4]
    meta = {
        "version": SUPPORTED_VERSION,
        "render_size": _RENDER_SIZE,
        "grid": _GRID,
        "characters": "".join(char for char, _ in entries),
        "ink_boxes": [list(feature[:4]) for _, feature in entries],
        "lookalikes": lookalikes,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _BIN_NAME).write_bytes(bytes(blob))
    (out_dir / _JSON_NAME).write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    raw_size = len(blob) + (out_dir / _JSON_NAME).stat().st_size
    gz_size = len(gzip.compress(bytes(blob), 9)) + len(gzip.compress((out_dir / _JSON_NAME).read_bytes(), 9))
    print(f"\n{len(entries)} glyphs indexed")
    print(f"  on disk : {raw_size / 1024:.0f} KB (gzipped {gz_size / 1024:.0f} KB)")


def main() -> int:
    """Regenerate the reference index in this package directory.

    :returns: Process exit status; ``1`` when the font is missing or the grid
        geometry is unusable.
    """
    import argparse
    import sys

    from PIL import ImageFont

    parser = argparse.ArgumentParser(
        description="Regenerate the reference glyph table for Yanxuan font de-obfuscation."
    )
    parser.add_argument("--font", default=_DEFAULT_FONT, help=f"reference font file (default: {_DEFAULT_FONT})")
    parser.add_argument(
        "--face", type=int, default=_DEFAULT_FACE, help=f"face index inside a TTC (default: {_DEFAULT_FACE})"
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent),
        help="directory to write the reference files into",
    )
    args = parser.parse_args()

    path = Path(args.font)
    if not path.exists():
        print(f"reference font not found: {path}", file=sys.stderr)
        return 1
    if _GRID % 8:
        print("GRID must be a multiple of 8 so a raster packs into whole bytes", file=sys.stderr)
        return 1

    font = ImageFont.truetype(str(path), _RENDER_SIZE, index=args.face)
    print(f"reference face: {path} (index {args.face}), render size {_RENDER_SIZE}, grid {_GRID}")
    _write_table(Path(args.out_dir), font)
    return 0


__all__ = [
    "NOTDEF_PROBE",
    "SUPPORTED_VERSION",
    "FontReference",
    "load_font_reference",
    "pack_raster",
    "raster_stride",
]


if __name__ == "__main__":
    raise SystemExit(main())
