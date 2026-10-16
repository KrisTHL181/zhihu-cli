#!/usr/bin/env python3
"""Regenerate the checked-in glyph reference table for Yanxuan font de-obfuscation.

Zhihu renders the body of paid-column section pages with a per-response subset
font whose ``cmap`` maps 80 codepoints to glyph names that equal the codepoint
(``U+4E2D`` -> ``uni4E2D``) while the *outline* draws a different character.
Reading the codepoints therefore yields garbled text; the rendered glyph is the
true character.  The substitution changes between responses, so no static
cipher table is possible — the glyphs must be identified by shape.

This script precomputes the shape reference: for every character of a fixed
charset it renders the glyph with the source face (``Noto Sans CJK SC``, which
is the face Zhihu's subset fonts copy their outlines from), crops to the ink
box and downsamples the crop to a ``GRID x GRID`` 1-bit raster.  Both the ink
box (relative to the glyph origin) and the raster are written to
``src/zhihu_cli/glyph_table/`` for the runtime matcher to consume.

Run it from the repository root::

    python tools/gen_yanxuan_font_table.py

Only Pillow and the standard library are used, so the script needs no optional
dependency.  It requires the source face to be installed; see ``--font``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: Default reference face: ``Noto Sans CJK SC`` is face index 2 of the TTC
#: (the TTC also carries the JP/KR/HK/TC faces).
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
DEFAULT_FACE = 2

#: Em size in pixels used to render every glyph.  The runtime decoder renders
#: the obfuscated font at the same size, so the two must agree.
RENDER_SIZE = 96

#: Edge of the square raster every glyph is normalised to.
GRID = 32

#: Rendered ink is compared to the render of this codepoint to tell a
#: substituted character from an ordinary one.  Any codepoint the font does not
#: cover renders as ``.notdef``, so a private-use codepoint is a safe probe.
NOTDEF_PROBE = ""

#: Unicode blocks whose characters are stored as look-alikes of ordinary
#: ideographs (e.g. ``U+2F08`` KANGXI RADICAL MAN instead of ``U+4EBA`` 人).
LOOKALIKE_BLOCKS = (
    (0x2E80, 0x2EFF),
    (0x2F00, 0x2FDF),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)

#: A look-alike is only resolved automatically when its raster is close to the
#: best-matching ideograph.  Radicals whose match is further away than this are
#: left alone: guessing would silently corrupt the text.
LOOKALIKE_MAX_DISTANCE = 64

#: Characters added on top of GB2312 level 1 — ASCII, CJK punctuation, the
#: fullwidth forms and the handful of general-punctuation marks that occur in
#: Chinese prose.
EXTRA_CHARACTERS = (
    "".join(chr(cp) for cp in range(0x21, 0x7F))
    + "".join(chr(cp) for cp in range(0x3000, 0x3040))
    + "".join(chr(cp) for cp in range(0xFF01, 0xFF5F))
    + "—…‘’“”·〈〉〖〗～￥°±×÷≈≤≥№→←↑↓①②③④⑤⑥⑦⑧⑨⑩"
)


def gb2312_level1() -> list[str]:
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


def character_set() -> list[str]:
    """Build the reference charset, de-duplicated and order-stable.

    :returns: List of characters to index.
    """
    return list(dict.fromkeys(gb2312_level1() + list(EXTRA_CHARACTERS)))


def glyph_feature(font: ImageFont.FreeTypeFont, char: str, size: int = RENDER_SIZE, grid: int = GRID):
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


def raster_to_int(raster: bytes) -> int:
    """Pack a 1-bit raster into an integer for fast Hamming comparison.

    :param raster: Bytes from :func:`glyph_feature`.
    :returns: The raster as a big-endian integer.
    """
    return int.from_bytes(raster, "big")


def build_lookalike_map(
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
    :param entries: ``(char, feature)`` pairs in table order.
    :returns: Mapping of look-alike character to unified ideograph.
    """
    rasters = [(char, raster_to_int(feature[4])) for char, feature in entries]
    resolved: dict[str, str] = {}
    for start, end in LOOKALIKE_BLOCKS:
        for cp in range(start, end + 1):
            char = chr(cp)
            if unicodedata.normalize("NFKC", char) != char:
                # NFKC already maps it; the runtime normalisation handles it.
                continue
            if unicodedata.name(char, None) is None:
                # Unassigned codepoint — renders as a placeholder, matches nothing.
                continue
            feature = glyph_feature(font, char)
            if feature is None:
                continue
            probe = raster_to_int(feature[4])
            best_char, best_distance = "", 1 << 30
            for candidate, candidate_raster in rasters:
                distance = (probe ^ candidate_raster).bit_count()
                if distance < best_distance:
                    best_char, best_distance = candidate, distance
            if best_char and best_distance <= LOOKALIKE_MAX_DISTANCE:
                resolved[char] = best_char
                print(f"  look-alike U+{cp:04X} {char} -> {best_char}  (distance {best_distance})")
    return resolved


def write_table(out_dir: Path, font: ImageFont.FreeTypeFont) -> None:
    """Render the charset and write the table files into *out_dir*.

    :param out_dir: Directory receiving ``glyph_table.bin`` / ``glyph_table.json``.
    :param font: Loaded reference face.
    """
    entries: list[tuple[str, tuple[int, int, int, int, bytes]]] = []
    skipped: list[str] = []
    for char in character_set():
        feature = glyph_feature(font, char)
        if feature is None:
            skipped.append(char)
            continue
        entries.append((char, feature))
    if skipped:
        print(f"skipped {len(skipped)} characters with no ink or a clipped glyph: {''.join(skipped)}")

    lookalikes = build_lookalike_map(font, entries)

    blob = bytearray()
    for char, feature in entries:
        blob += feature[4]
    meta = {
        "version": 1,
        "render_size": RENDER_SIZE,
        "grid": GRID,
        "characters": "".join(char for char, _ in entries),
        "ink_boxes": [list(feature[:4]) for _, feature in entries],
        "lookalikes": lookalikes,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "glyph_table.bin").write_bytes(bytes(blob))
    (out_dir / "glyph_table.json").write_text(
        json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    raw_size = len(blob) + (out_dir / "glyph_table.json").stat().st_size
    gz_size = len(gzip.compress(bytes(blob), 9)) + len(gzip.compress((out_dir / "glyph_table.json").read_bytes(), 9))
    print(f"\n{len(entries)} glyphs indexed")
    print(f"  on disk : {raw_size / 1024:.0f} KB (gzipped {gz_size / 1024:.0f} KB)")


def main() -> int:
    """Entry point.

    :returns: Process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font", default=DEFAULT_FONT, help=f"reference font file (default: {DEFAULT_FONT})")
    parser.add_argument(
        "--face", type=int, default=DEFAULT_FACE, help=f"face index inside a TTC (default: {DEFAULT_FACE})"
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent.parent / "src" / "zhihu_cli" / "glyph_table"),
        help="directory to write the table into",
    )
    args = parser.parse_args()

    path = Path(args.font)
    if not path.exists():
        print(f"reference font not found: {path}", file=sys.stderr)
        return 1
    if GRID % 8:
        print("GRID must be a multiple of 8 so a raster packs into whole bytes", file=sys.stderr)
        return 1

    font = ImageFont.truetype(str(path), RENDER_SIZE, index=args.face)
    print(f"reference face: {path} (index {args.face}), render size {RENDER_SIZE}, grid {GRID}")
    write_table(Path(args.out_dir), font)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
