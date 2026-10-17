"""Burn a text overlay into the rendered video.

``drawtext``/``subtitles`` require an FFmpeg built with libfreetype/libass, which
is not guaranteed — the Homebrew build used here has neither (and no other text
filter either). So the text is rasterised to a transparent PNG the exact size of
the output frame with Pillow and composited using the ``overlay`` filter, which
ships with every FFmpeg build.

Doing the layout ourselves also gives us real multi-line text with word wrapping
and an outline for legibility, instead of ``drawtext``'s escaping minefield.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from ..errors import PianoSyncError

#: Font files tried in order when nothing is chosen; the first one that exists
#: wins. ``PIANOSYNC_OVERLAY_FONT`` overrides the whole search and accepts either
#: a family name or a font file path.
FONT_CANDIDATES: Tuple[str, ...] = (
    # macOS — Korean + Latin in one family
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows
    "C:\\Windows\\Fonts\\malgun.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)

#: Directories scanned for the fonts offered to the user (per platform).
FONT_DIRS: Tuple[str, ...] = (
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.join(os.path.expanduser("~"), "Library", "Fonts"),
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.join(os.path.expanduser("~"), ".local", "share", "fonts"),
    os.path.join(os.path.expanduser("~"), ".fonts"),
    "C:\\Windows\\Fonts",
)

_FONT_EXTS = (".ttf", ".ttc", ".otf", ".otc")
_MAX_FACES = 32  # faces walked inside a font collection

#: Families offered on top of every Korean-capable system font, so English
#: captions are not limited to whatever Korean font happened to be installed.
COMMON_LATIN_FAMILIES: Tuple[str, ...] = (
    "Pretendard",
    "Pretendard Variable",
    "Helvetica Neue",
    "Helvetica",
    "Arial",
    "Avenir Next",
    "Verdana",
    "Trebuchet MS",
    "Tahoma",
    "Futura",
    "Gill Sans",
    "Optima",
    "Georgia",
    "Times New Roman",
    "Palatino",
    "Baskerville",
    "Menlo",
    "Monaco",
    "Courier New",
    "Noto Sans",
    "Noto Sans KR",
    "DejaVu Sans",
)

#: macOS keeps dozens of internal faces (``.Apple SD Gothic NeoI``, ``.SF
#: Arabic``, ``.CJK Symbols Fallback JA`` …) that nobody should pick for a
#: caption, so they are filtered out of the menu.
_INTERNAL_MARKERS = (
    "PUA",
    "Fallback",
    "Interface",
    "Numeric",
    "Watch",
    "LastResort",
    "Descriptor",
)

POSITION_TOP = "top"
POSITION_CENTER = "center"
POSITION_BOTTOM = "bottom"
POSITIONS: Tuple[str, ...] = (POSITION_TOP, POSITION_CENTER, POSITION_BOTTOM)

#: Vertical band for each position, as a multiple of the frame height.
POSITION_FACTORS = {
    POSITION_TOP: 0.055,
    POSITION_CENTER: 0.5,
    POSITION_BOTTOM: 0.945,
}

#: Cap height as a multiple of the frame height.
SIZE_FACTORS = {"small": 0.038, "medium": 0.056, "large": 0.080}
SIZES: Tuple[str, ...] = ("small", "medium", "large")

#: Text colour and the matching outline colour (always the opposite, so the
#: text stays readable on any background).
COLOR_RGB = {"white": (255, 255, 255), "black": (0, 0, 0)}
COLORS: Tuple[str, ...] = ("white", "black")

OVERLAY_DURATION_FIXED = "fixed"
OVERLAY_DURATIONS: Tuple[str, ...] = (OVERLAY_DURATION_FIXED, "3", "5", "10")

#: Style names preferred when the font file is a collection (.ttc).
_PREFERRED_STYLES = ("Bold", "SemiBold", "Medium")

_MAX_TEXT_WIDTH = 0.88  # of the frame width
_LINE_SPACING = 1.24
_STROKE_RATIO = 0.062


@dataclass(frozen=True)
class FontFace:
    """One selectable font: a family resolved to a concrete file and face."""

    family: str
    style: str
    path: str
    index: int
    hangul: bool


#: Hangul syllable blocks, compatibility jamo and the jamo blocks.
_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7A3),
    (0xD7B0, 0xD7FF),
)


def normalize_position(value: Optional[str]) -> str:
    return value if value in POSITIONS else POSITION_BOTTOM


def normalize_size(value: Optional[str]) -> str:
    return value if value in SIZES else "medium"


def normalize_color(value: Optional[str]) -> str:
    return value if value in COLORS else "white"


def normalize_overlay_duration(value: Optional[str]) -> str:
    """``fixed`` = entire video; ``3``/``5``/``10`` = visible for that many seconds."""
    raw = (value or "").strip().lower()
    if raw in ("", "fixed", "0", "full", "all", "none"):
        return OVERLAY_DURATION_FIXED
    if raw in ("3", "5", "10"):
        return raw
    return OVERLAY_DURATION_FIXED


def overlay_duration_seconds(choice: Optional[str]) -> Optional[float]:
    """Seconds to show the caption, or ``None`` for the full output length."""
    normalized = normalize_overlay_duration(choice)
    if normalized == OVERLAY_DURATION_FIXED:
        return None
    return float(normalized)


def _face_name(font) -> Tuple[str, str]:
    try:
        family, style = font.getname()
    except Exception:  # noqa: BLE001 - nameless faces are not useful anyway
        return "", ""
    return (family or "").strip(), (style or "").strip()


def _is_internal(family: str) -> bool:
    """True for OS-internal faces (``.Apple SD Gothic NeoI``, ``… Fallback``)."""
    if not family:
        return True
    return family.startswith(".") or any(marker in family for marker in _INTERNAL_MARKERS)


def _draws(char: str, font) -> bool:
    """True when ``font`` has a real glyph for ``char`` (not blank or .notdef)."""
    try:
        mask = font.getmask(char)
        notdef = font.getmask("\ue123")  # private-use: never mapped by real fonts
    except Exception:  # noqa: BLE001
        return False
    if mask.getbbox() is None:
        return False
    return bytes(mask) != bytes(notdef)


def _has_hangul(font, chars: Sequence[str] = ("가", "한", "글")) -> bool:
    return all(_draws(char, font) for char in chars)


@lru_cache(maxsize=1)
def _font_files() -> Tuple[str, ...]:
    found: List[str] = []
    for directory in FONT_DIRS:
        if not os.path.isdir(directory):
            continue
        for root, _dirs, names in os.walk(directory):
            found.extend(
                os.path.join(root, name)
                for name in names
                if name.lower().endswith(_FONT_EXTS)
            )
    return tuple(sorted(found))


def _best_face_index(path: str) -> int:
    """Index of the boldest face inside a collection (0 for plain font files)."""
    if not path.lower().endswith((".ttc", ".otc")):
        return 0
    from PIL import ImageFont

    best_index, best_rank = 0, len(_PREFERRED_STYLES)
    for index in range(32):
        try:
            style = _face_name(ImageFont.truetype(path, 32, index=index))[1]
        except Exception:  # noqa: BLE001 - past the last face
            break
        if style in _PREFERRED_STYLES:
            rank = _PREFERRED_STYLES.index(style)
            if rank < best_rank:
                best_index, best_rank = index, rank
    return best_index


@lru_cache(maxsize=1)
def list_fonts() -> Tuple[FontFace, ...]:
    """Fonts offered in the UI, Korean-capable families first.

    Every family that can draw Hangul is included, plus a small set of common
    Latin families (:data:`COMMON_LATIN_FAMILIES`) so Latin-only captions are
    not limited to whatever installed Korean font happens to be present. Each
    family is collapsed to a single (boldest) face to keep the menu readable.
    """
    try:
        from PIL import ImageFont
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        return ()

    latin = {name.casefold() for name in COMMON_LATIN_FAMILIES}
    best: Dict[str, Tuple[int, FontFace]] = {}

    for path in _font_files():
        limit = 32 if path.lower().endswith((".ttc", ".otc")) else 1
        for index in range(limit):
            try:
                probe = ImageFont.truetype(path, 32, index=index)
            except Exception:  # noqa: BLE001 - past the last face / unreadable file
                break
            family, style = _face_name(probe)
            if _is_internal(family):
                continue
            hangul = _has_hangul(probe)
            if not hangul and family.casefold() not in latin:
                continue
            # Prefer the boldest face of a family; later files do not override it.
            rank = _PREFERRED_STYLES.index(style) if style in _PREFERRED_STYLES else len(_PREFERRED_STYLES)
            current = best.get(family)
            if current is None or rank < current[0]:
                best[family] = (rank, FontFace(family, style, path, index, hangul))

    faces = [face for _rank, face in best.values()]
    # Korean-capable families first — that is what captions are usually set in.
    faces.sort(key=lambda face: (not face.hangul, face.family.casefold()))
    return tuple(faces)


def find_font_path() -> Optional[str]:
    """The automatic default font file, honouring ``PIANOSYNC_OVERLAY_FONT``."""
    override = os.environ.get("PIANOSYNC_OVERLAY_FONT")
    if override:
        if os.path.isfile(override):
            return override
        wanted = override.strip().casefold()
        for face in list_fonts():
            if face.family.casefold() == wanted:
                return face.path
        return None
    for candidate in FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return None


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value or value.lower().endswith((".ttf", ".ttc", ".otf", ".otc"))


def servable_font_path(path: str) -> str:
    """Return a normalised font file path the browser may fetch for UI preview.

    Only files under :data:`FONT_DIRS` (or already known to :func:`list_fonts`)
    are allowed so the endpoint cannot be used as an arbitrary file reader.
    """
    raw = (path or "").strip()
    if not raw or not _looks_like_path(raw):
        raise PianoSyncError("글꼴 파일 경로가 아닙니다.")
    resolved = os.path.realpath(os.path.expanduser(raw))
    if not os.path.isfile(resolved):
        raise PianoSyncError(f"글꼴 파일을 찾을 수 없습니다: {raw}")
    if not resolved.lower().endswith(_FONT_EXTS):
        raise PianoSyncError("지원하지 않는 글꼴 형식입니다 (.ttf, .ttc, .otf, .otc).")

    allowed_roots = tuple(
        os.path.realpath(os.path.expanduser(d)) for d in FONT_DIRS if os.path.isdir(d)
    )
    under_scan_dir = any(
        resolved == root or resolved.startswith(root + os.sep) for root in allowed_roots
    )
    known_paths = {face.path for face in list_fonts()}
    if not under_scan_dir and resolved not in known_paths:
        raise PianoSyncError("미리보기에 허용되지 않은 글꼴 경로입니다.")
    return resolved


def default_preview_family() -> str:
    """Family name shown in the UI when overlay font is ``automatic``."""
    path = find_font_path()
    if not path:
        return ""
    for face in list_fonts():
        if face.path == path:
            return face.family
    return ""


def resolve_font(choice: Optional[str] = None) -> Tuple[Optional[str], Optional[int]]:
    """Resolve a stored font choice to ``(file path, face index)``.

    ``choice`` may be a family name from :func:`list_fonts`, a font file path, or
    empty/None for the automatic default. ``index`` is ``None`` when the boldest
    face of the file should be used. A family that no longer exists on this
    machine (a project opened elsewhere, say) falls back to the default, while a
    mistyped *path* is reported instead of being silently ignored.
    """
    choice = (choice or "").strip()
    if not choice:
        return (find_font_path(), None)
    if _looks_like_path(choice):
        if not os.path.isfile(choice):
            raise PianoSyncError(f"글꼴 파일을 찾을 수 없습니다: {choice}")
        return (choice, None)
    wanted = choice.casefold()
    for face in list_fonts():
        if face.family.casefold() == wanted:
            return (face.path, face.index)
    return (find_font_path(), None)


def font_choices() -> List[Tuple[str, str]]:
    """``(value, label)`` pairs for a font menu; value ``""`` means automatic."""
    options = [("", "자동 (시스템 기본)")]
    options += [
        (face.family, face.family if face.hangul else f"{face.family} · 영문")
        for face in list_fonts()
    ]
    return options


def missing_hangul(font, text: str) -> List[str]:
    """Hangul characters in ``text`` that ``font`` cannot draw."""
    missing: List[str] = []
    for char in dict.fromkeys(text):
        if not any(low <= ord(char) <= high for low, high in _HANGUL_RANGES):
            continue
        if not _draws(char, font):
            missing.append(char)
    return missing


def _load_font(path: str, px: int, index: Optional[int] = None):
    """Load ``px``-sized font; ``index=None`` picks the boldest face of a collection."""
    from PIL import ImageFont

    wanted = _best_face_index(path) if index is None else index
    for candidate in (wanted, 0):
        try:
            return ImageFont.truetype(path, px, index=candidate)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    raise PianoSyncError(f"글꼴을 불러올 수 없습니다: {path}")



def _wrap_lines(draw, text: str, font, max_width: float, stroke: int) -> List[str]:
    """Split ``text`` into lines that fit ``max_width``.

    Breaks on spaces like a text editor, and falls back to breaking mid-token so
    that space-free scripts (Korean sentences without spaces, URLs, …) still fit.
    """
    lines: List[str] = []

    def measure(candidate: str) -> float:
        return draw.textlength(candidate, font=font) + 2 * stroke

    for paragraph in text.splitlines() or [""]:
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if measure(candidate) <= max_width or not current:
                # A single word wider than the line has to be cut by character.
                if not current and measure(word) > max_width:
                    chunk = ""
                    for char in word:
                        if measure(chunk + char) > max_width and chunk:
                            lines.append(chunk)
                            chunk = char
                        else:
                            chunk += char
                    current = chunk
                else:
                    current = candidate
            else:
                lines.append(current)
                current = word
                if measure(current) > max_width:
                    chunk = ""
                    for char in current:
                        if measure(chunk + char) > max_width and chunk:
                            lines.append(chunk)
                            chunk = char
                        else:
                            chunk += char
                    current = chunk
        lines.append(current)
    return lines or [""]


def render_overlay_png(
    text: str,
    width: int,
    height: int,
    out_path: str,
    *,
    position: str = POSITION_BOTTOM,
    size: str = "medium",
    color: str = "white",
    font: Optional[str] = None,
) -> Optional[str]:
    """Rasterise ``text`` onto a transparent ``width`` x ``height`` PNG.

    ``font`` picks the typeface: a family name from :func:`list_fonts`, a font
    file path, or ``None``/``""`` for the automatic default (see
    :func:`resolve_font`).

    Returns ``out_path``, or ``None`` when there is nothing to draw. Raises
    :class:`~pianosync.errors.PianoSyncError` when Pillow or a usable font is
    missing, or when the chosen font cannot draw the Hangul in ``text``.
    """
    if not text or not text.strip():
        return None
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PianoSyncError(
            "텍스트 오버레이를 그리려면 Pillow가 필요합니다 (pip install Pillow)."
        ) from exc

    path, index = resolve_font(font)
    if not path:
        raise PianoSyncError(
            "오버레이에 쓸 글꼴을 찾지 못했습니다. 글꼴을 고르거나 "
            "PIANOSYNC_OVERLAY_FONT 환경변수로 글꼴 파일 경로를 지정해 주세요."
        )

    width = max(2, int(width))
    height = max(2, int(height))
    font_px = max(12, int(round(height * SIZE_FACTORS[normalize_size(size)])))
    stroke = max(2, int(round(font_px * _STROKE_RATIO)))
    face = _load_font(path, font_px, index)

    missing = missing_hangul(face, text)
    if missing:
        shown = " ".join(missing[:12]) + (" …" if len(missing) > 12 else "")
        raise PianoSyncError(
            f"선택한 글꼴에 한글 글자가 없습니다: {shown} — 한글 글꼴을 고르거나 "
            "'자동'을 사용해 주세요."
        )

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    lines = _wrap_lines(draw, text.strip(), face, width * _MAX_TEXT_WIDTH, stroke)
    if not lines:
        return None

    line_height = font_px * _LINE_SPACING
    block_height = line_height * len(lines)
    anchor_y = POSITION_FACTORS[normalize_position(position)] * height
    y0 = anchor_y if position == POSITION_TOP else anchor_y - block_height / 2
    if position == POSITION_BOTTOM:
        y0 = anchor_y - block_height
    y0 = max(stroke + 1.0, min(y0, height - block_height - stroke - 1.0))

    fill = COLOR_RGB[normalize_color(color)]
    stroke_fill = COLOR_RGB["black" if color == "white" else "white"]

    for index, line in enumerate(lines):
        box = draw.textbbox((0, 0), line, font=face, stroke_width=stroke)
        x = (width - (box[2] - box[0])) / 2 - box[0]
        draw.text(
            (x, y0 + index * line_height),
            line,
            font=face,
            fill=fill,
            stroke_width=stroke,
            stroke_fill=stroke_fill,
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    image.save(out_path, format="PNG")
    return out_path
