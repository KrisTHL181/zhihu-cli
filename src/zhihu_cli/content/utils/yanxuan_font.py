"""Decode the glyph-substitution cipher Zhihu applies to Yanxuan body text.

Paid-column section pages (``/market/paid_column/<id>/section/<id>``) ship the
full text, but the high-frequency characters of the body are drawn with a
per-response subset font: its ``cmap`` maps 80 codepoints to glyphs named after
the codepoint (``U+4E2D`` -> ``uni4E2D``) while the outline draws a *different*
character.  Reading the codepoints gives garbled text; the rendered glyph is the
true character.  A browser is unaffected, because the codepoints the font does
not cover fall through to the next family in the CSS stack.

The font is regenerated for many responses — both the 80 covered codepoints and
the 80 substituted characters change — so decoding is always driven by the font
that arrived with the response, never by a static cipher table:

1. The subset font is loaded from the base64 payload.
2. Any character that renders exactly like a codepoint the font cannot have (a
   private-use ``.notdef`` probe) is *not* substituted and is left alone.  No
   ``cmap`` parsing is needed for this.
3. Every other character is rendered, cropped to its ink box and matched against
   the checked-in reference rasters (:mod:`zhihu_cli.glyph_table`).  The ink box
   relative to the glyph origin narrows the candidates, the raster Hamming
   distance decides.
4. A match that is not clearly better than the runner-up is rejected, so an
   unknown glyph degrades to the original character instead of a wrong one.
5. Plaintext look-alikes from the radical blocks (``U+2F08`` ⼈ instead of 人)
   are folded to the unified ideograph.

Pillow is imported lazily inside the functions: this module sits on the CLI
startup path only as a lazy import, and keeping the import local makes that
obvious.  Per-font mappings are cached by the font's SHA-256, so a process that
decodes many responses pays for each font generation once.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import unicodedata
from typing import TYPE_CHECKING, NamedTuple

from zhihu_cli.glyph_table import NOTDEF_PROBE, GlyphTable, load_glyph_table, pack_raster

if TYPE_CHECKING:
    from PIL import ImageFont

#: Maximum Hamming distance (out of ``grid ** 2`` bits) accepted as a match.
#: Correct matches measured against the three known font generations stay below
#: 80; a glyph that is missing from the reference table lands far above this.
MAX_MATCH_DISTANCE = 130

#: Minimum lead of the best match over the runner-up.  Measured margins for
#: correct matches are 50 or more; a smaller lead means the shape is ambiguous.
MIN_MATCH_MARGIN = 20

#: Tolerance in pixels when narrowing candidates by ink box.  A number of the
#: obfuscated glyphs have outlines perturbed by a few font units, which shifts
#: the rasterised box by up to two pixels; measured against the known
#: generations, two is the smallest tolerance that identifies every glyph.
INK_BOX_TOLERANCE = 2

#: Unicode blocks holding look-alikes of ordinary ideographs.  NFKC maps the
#: Kangxi radicals and the compatibility ideographs to their unified form; the
#: CJK Radicals Supplement mostly has no mapping and is covered by the
#: ``lookalikes`` map the generator writes into the table.
LOOKALIKE_BLOCKS = (
    (0x2E80, 0x2EFF),
    (0x2F00, 0x2FDF),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)

_NO_MATCH = 1 << 30


class DecodeReport(NamedTuple):
    """Result of a decode run.

    :ivar text: The decoded text.
    :ivar mapping: Substituted character to decoded character, restricted to the
        characters that occur in the input.
    :ivar low_confidence: Characters that looked substituted but could not be
        identified with enough confidence, and were therefore left as they are.
    """

    text: str
    mapping: dict[str, str]
    low_confidence: list[str]


class _RenderedGlyph(NamedTuple):
    """Shape of one rendered glyph.

    :ivar ink_box: Ink extent relative to the glyph origin, as ``(x0, y0, x1, y1)``.
    :ivar raster: The cropped ink packed into a ``grid * grid`` bit integer.
    """

    ink_box: tuple[int, int, int, int]
    raster: int


class _FontDecoder:
    """Per-font substitution decoder.

    Holds the loaded subset font and memoises what every codepoint it has been
    asked about decoded to, so repeated decodes with the same font generation
    reuse the shape matching.

    :param table: Reference glyph table.
    :param font_bytes: Raw bytes of the subset TTF.
    """

    def __init__(self, table: GlyphTable, font_bytes: bytes) -> None:
        self._table = table
        self._font = _load_font(font_bytes, table.render_size)
        self._cube = _load_notdef(self._font, table)
        self._cache: dict[str, str | None] = {}
        self._ambiguous: set[str] = set()
        self._by_ink_box: dict[tuple[int, int, int, int], list[int]] = {}
        for index, box in enumerate(table.ink_boxes):
            self._by_ink_box.setdefault(box, []).append(index)

    def decode(self, char: str) -> str:
        """Decode a single character.

        :param char: Character read from the SSR payload.
        :returns: The character the glyph draws, or *char* itself when it is not
            substituted, or is substituted but cannot be identified.
        """
        if char in self._cache:
            resolved = self._cache[char]
            return resolved if resolved is not None else char

        rendered = _render_glyph(self._font, char, self._table)
        if rendered is None or rendered.raster == self._cube:
            # No ink, no glyph for this codepoint, or the font's ``.notdef`` box:
            # the browser would fall back to the next family, so this character
            # is plaintext.
            self._cache[char] = None
            return char

        match = self._match(rendered)
        self._cache[char] = match
        if match is None:
            self._ambiguous.add(char)
            return char
        return match

    @property
    def ambiguous(self) -> set[str]:
        """Characters left alone because no match was confident enough.

        :returns: A copy of the undecided-character set.
        """
        return set(self._ambiguous)

    def _match(self, rendered: _RenderedGlyph) -> str | None:
        """Identify the reference character drawn by *rendered*.

        :param rendered: Ink box and packed raster of the queried glyph.
        :returns: The matched character, or ``None`` when nothing is convincing.
        """
        candidates = self._candidates(rendered.ink_box)
        best_char, best_distance, runner_up = self._scan(rendered.raster, candidates)
        if candidates is not None and best_distance > MAX_MATCH_DISTANCE:
            # Nothing inside the ink-box window fits, which means the window
            # itself is suspect (a more heavily perturbed outline, say) — retry
            # over the whole table before giving up.
            best_char, best_distance, runner_up = self._scan(rendered.raster, None)
        if not best_char or best_distance > MAX_MATCH_DISTANCE:
            return None
        if runner_up != _NO_MATCH and runner_up - best_distance < MIN_MATCH_MARGIN:
            return None
        return best_char

    def _scan(self, raster: int, indices: list[int] | None) -> tuple[str, int, int]:
        """Find the two closest reference rasters.

        :param raster: Packed raster of the queried glyph.
        :param indices: Table indices to search, or ``None`` for the whole table.
        :returns: ``(best_char, best_distance, runner_up_distance)``; the
            character is empty when nothing was searched.
        """
        rasters = self._table.rasters
        characters = self._table.characters
        best_char = ""
        best_distance = _NO_MATCH
        runner_up = _NO_MATCH
        for index in range(len(rasters)) if indices is None else indices:
            distance = (raster ^ rasters[index]).bit_count()
            if distance < best_distance:
                runner_up = best_distance
                best_char, best_distance = characters[index], distance
            elif distance < runner_up:
                runner_up = distance
        return best_char, best_distance, runner_up

    def _candidates(self, ink_box: tuple[int, int, int, int]) -> list[int] | None:
        """Index the reference glyphs whose ink box is near *ink_box*.

        :param ink_box: Ink box of the queried glyph, relative to its origin.
        :returns: Candidate indices, or ``None`` when no box is close enough.
        """
        offsets = range(-INK_BOX_TOLERANCE, INK_BOX_TOLERANCE + 1)
        x0, y0, x1, y1 = ink_box
        found: set[int] = set()
        for dx0 in offsets:
            for dy0 in offsets:
                for dx1 in offsets:
                    for dy1 in offsets:
                        indices = self._by_ink_box.get((x0 + dx0, y0 + dy0, x1 + dx1, y1 + dy1))
                        if indices:
                            found.update(indices)
        return sorted(found) or None


_decoders: dict[str, _FontDecoder] = {}


def decode_manuscript(text: str, font_base64: str) -> str:
    """Decode a font-obfuscated Yanxuan manuscript.

    :param text: Plain text whose glyphs were drawn with the subset *font_base64*.
    :param font_base64: The ``font.base64`` data URL shipped with the response.
    :returns: The decoded text.  Characters the cipher did not touch, and
        characters that could not be identified, are returned unchanged.
    """
    return decode_manuscript_report(text, font_base64).text


def decode_manuscript_report(text: str, font_base64: str) -> DecodeReport:
    """Decode a manuscript and report what was decided.

    :param text: Plain text whose glyphs were drawn with the subset *font_base64*.
    :param font_base64: The ``font.base64`` data URL shipped with the response.
    :returns: A :class:`DecodeReport` carrying the decoded text, the substitution
        map actually applied, and the characters left undecided.
    :raises ValueError: If *font_base64* is not a usable base64 font payload.
    """
    font_bytes = extract_font_bytes(font_base64)
    if not font_bytes:
        return DecodeReport(text=normalize_lookalikes(text), mapping={}, low_confidence=[])

    decoder = _decoder_for(font_bytes)
    mapping: dict[str, str] = {}
    for char in dict.fromkeys(text):
        decoded = decoder.decode(char)
        if decoded != char:
            mapping[char] = decoded
    decoded_text = "".join(mapping.get(char, char) for char in text)
    return DecodeReport(
        text=normalize_lookalikes(decoded_text),
        mapping=mapping,
        low_confidence=sorted(decoder.ambiguous - set(mapping)),
    )


def extract_font_bytes(font_base64: str) -> bytes:
    """Decode the base64 font payload carried by the ``font`` field.

    Accepts a bare base64 string as well as a ``data:font/ttf;base64,…`` URL.

    :param font_base64: Raw field value from the SSR state.
    :returns: Font bytes, or ``b""`` when the field is empty.
    :raises ValueError: If the payload is not valid base64.
    """
    payload = font_base64.strip()
    if not payload:
        return b""
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"font payload is not valid base64: {exc}") from exc


def normalize_lookalikes(text: str) -> str:
    """Fold radical-block look-alikes to the unified ideograph they render as.

    Only the characters in those blocks are normalised: a blanket ``NFKC`` pass
    over the whole text would also rewrite fullwidth punctuation (``！``, ``：``,
    ``，``) to its ASCII form and corrupt the prose.

    :param text: Decoded text.
    :returns: Text with look-alike characters replaced.
    """
    table = load_glyph_table()
    out: list[str] = []
    for char in text:
        codepoint = ord(char)
        if not any(start <= codepoint <= end for start, end in LOOKALIKE_BLOCKS):
            out.append(char)
            continue
        folded = unicodedata.normalize("NFKC", char)
        if folded == char:
            folded = table.lookalikes.get(char, char)
        out.append(folded)
    return "".join(out)


def _decoder_for(font_bytes: bytes) -> _FontDecoder:
    """Return the cached decoder for *font_bytes*, building it on first use.

    :param font_bytes: Raw bytes of the subset TTF.
    :returns: A decoder bound to that font generation.
    """
    key = hashlib.sha256(font_bytes).hexdigest()
    decoder = _decoders.get(key)
    if decoder is None:
        decoder = _FontDecoder(load_glyph_table(), font_bytes)
        _decoders[key] = decoder
    return decoder


def _load_font(font_bytes: bytes, render_size: int) -> ImageFont.FreeTypeFont:
    """Load the subset font from memory — no temporary file involved.

    :param font_bytes: Raw bytes of the subset TTF.
    :param render_size: Em size in pixels, matching the reference table.
    :returns: A Pillow font object.
    """
    from PIL import ImageFont

    return ImageFont.truetype(io.BytesIO(font_bytes), render_size)


def _load_notdef(font: ImageFont.FreeTypeFont, table: GlyphTable) -> int:
    """Render the probe codepoint every uncovered character renders as.

    :param font: Subset font.
    :param table: Reference table supplying the grid geometry.
    :returns: Packed raster of the ``.notdef`` glyph, or ``-1`` when it has no
        ink, in which case nothing can be excluded by this test.
    """
    rendered = _render_glyph(font, NOTDEF_PROBE, table)
    return -1 if rendered is None else rendered.raster


def _render_glyph(font: ImageFont.FreeTypeFont, char: str, table: GlyphTable) -> _RenderedGlyph | None:
    """Render *char* and reduce it to the table's feature form.

    The glyph is drawn at a fixed baseline origin so that its ink box is
    comparable with the one recorded in the table.

    :param font: Font to render with.
    :param char: Character to render.
    :param table: Reference table supplying the render size and grid.
    :returns: The rendered glyph, or ``None`` when it has no ink or would be
        clipped by the drawing canvas.
    """
    from PIL import Image, ImageDraw

    size = table.render_size
    canvas = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(canvas).text((size, size), char, font=font, fill=255, anchor="ls")
    box = canvas.getbbox()
    if box is None:
        return None
    x0, y0, x1, y1 = box
    edge = size * 3
    if x0 <= 0 or y0 <= 0 or x1 >= edge or y1 >= edge:
        # Ink runs into the canvas edge, so the captured shape would be a crop
        # of the real glyph.  Refuse rather than match something wrong.
        return None
    small = canvas.crop(box).resize((table.grid, table.grid), Image.BILINEAR).convert("1", dither=Image.Dither.NONE)
    return _RenderedGlyph((x0 - size, y0 - size, x1 - size, y1 - size), pack_raster(small.tobytes(), table.grid))


__all__ = [
    "INK_BOX_TOLERANCE",
    "LOOKALIKE_BLOCKS",
    "MAX_MATCH_DISTANCE",
    "MIN_MATCH_MARGIN",
    "DecodeReport",
    "decode_manuscript",
    "decode_manuscript_report",
    "extract_font_bytes",
    "normalize_lookalikes",
]
