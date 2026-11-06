"""Dramatic rendering for a single solo performance.

The comparison renderer cuts between several videos.  This renderer keeps one
continuous performance and adds cinematic emphasis instead:

* a slow push-in with gentle lateral drift and hand-held micro shake,
* a short punch-in (and small focal shift) that starts exactly on each impact,
* a brightness flash on those moments,
* a soft bloom, colour grade, vignette and film grain,
* optional letterbox bars and a generated intro title card.

Everything is expressed as an FFmpeg filter graph with time-varying
expressions, so there is no per-frame Python work and audio stays in sync.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..audio.extract import MediaInfo, find_ffmpeg
from ..config import OutputConfig
from ..core.drama import DramaPlan, ImpactMoment
from ..core.progress import Progress
from ..errors import PianoSyncError
from .mapping import resolve_output_size
from .overlay import find_font_path, missing_hangul, resolve_font
from .render import run_ffmpeg_with_progress


@dataclass(frozen=True)
class SoloStyle:
    """Colour/atmosphere recipe for one preset."""

    key: str
    label: str
    contrast: float
    saturation: float
    gamma: float
    balance: Tuple[float, float, float]
    glow: float
    vignette: float
    grain: bool
    mono: bool
    accent: Tuple[int, int, int]


SOLO_STYLES: Dict[str, SoloStyle] = {
    "cinematic": SoloStyle(
        "cinematic", "시네마틱", contrast=1.09, saturation=1.06, gamma=0.98,
        balance=(0.020, -0.005, -0.018), glow=0.16, vignette=0.58,
        grain=True, mono=False, accent=(62, 207, 142),
    ),
    "noir": SoloStyle(
        "noir", "느와르", contrast=1.24, saturation=1.0, gamma=0.94,
        balance=(0.0, 0.0, 0.0), glow=0.10, vignette=0.72,
        grain=True, mono=True, accent=(225, 225, 225),
    ),
    "neon": SoloStyle(
        "neon", "네온", contrast=1.15, saturation=1.34, gamma=0.96,
        balance=(-0.035, 0.010, 0.060), glow=0.24, vignette=0.52,
        grain=False, mono=False, accent=(96, 214, 255),
    ),
    "warm": SoloStyle(
        "warm", "웜", contrast=1.05, saturation=1.12, gamma=1.03,
        balance=(0.055, 0.015, -0.040), glow=0.14, vignette=0.46,
        grain=True, mono=False, accent=(255, 188, 118),
    ),
    "clean": SoloStyle(
        "clean", "클린", contrast=1.04, saturation=1.03, gamma=1.0,
        balance=(0.0, 0.0, 0.0), glow=0.06, vignette=0.30,
        grain=False, mono=False, accent=(231, 233, 236),
    ),
}

SOLO_STYLE_KEYS = tuple(SOLO_STYLES.keys())


def normalize_style(value: Optional[str]) -> str:
    return value if value in SOLO_STYLES else "cinematic"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass
class SoloConfig:
    """User-facing dramatic-edit options (separate from output encoding)."""

    style: str = "cinematic"
    intensity: float = 0.6
    camera: bool = True
    punch: bool = True
    flash: bool = True
    glow: bool = True
    vignette: bool = True
    grain: bool = True
    letterbox: bool = False
    intro_text: str = ""
    intro_subtitle: str = ""
    intro_seconds: float = 2.5
    normalize_audio: bool = True
    include_audio: bool = True

    def normalized(self) -> "SoloConfig":
        return replace(
            self,
            style=normalize_style(self.style),
            intensity=_clamp(self.intensity, 0.0, 1.0),
            intro_text=(self.intro_text or "").strip(),
            intro_subtitle=(self.intro_subtitle or "").strip(),
            intro_seconds=_clamp(self.intro_seconds, 0.0, 8.0),
        )


@dataclass
class SoloRenderResult:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    moments: int
    style: str
    intro_seconds: float


def _style(config: SoloConfig) -> SoloStyle:
    return SOLO_STYLES[normalize_style(config.style)]


def _escape(expression: str) -> str:
    """Escape commas for FFmpeg's filter argument parser."""
    return expression.replace(",", "\\,")


def _impact_terms(
    moments: Sequence[ImpactMoment],
    time_expr: str,
    decay: float,
    amount,
) -> str:
    """Post-hit pulses that start exactly on the note and fade out afterwards.

    The old symmetric triangular pulse was already rising *before* the hit,
    which made the picture feel disconnected from the attack.  This one is
    gated by ``gte`` so the movement begins on the onset and decays over
    ``decay`` seconds.
    """
    terms = []
    for moment in moments:
        value = float(amount(moment))
        if value <= 0.0005:
            continue
        hit = f"{moment.time:.4f}"
        terms.append(
            f"{value:.4f}*gte({time_expr},{hit})*"
            f"pow(max(0,1-({time_expr}-{hit})/{decay:.4f}),2)"
        )
    return "+".join(terms) if terms else "0"


def _camera_expressions(
    moments: Sequence[ImpactMoment],
    fps: float,
    duration: float,
    intensity: float,
    camera: bool,
) -> Tuple[str, str, str]:
    """Return ``(zoom, x, y)`` expressions for a handheld cinematic camera.

    * a slow push-in for the whole performance,
    * a gentle lateral drift (``camera=True``),
    * subtle hand-held micro shake (``camera=True``),
    * a short punch-in and lateral nudge exactly on each impact.
    """
    fps = max(1.0, float(fps))
    duration = max(0.25, float(duration))
    t = f"on/{fps:.4f}"
    progress = f"min(1,on/{fps:.4f}/{duration:.4f})"
    zoom = f"1.012+{0.020 + 0.045 * intensity:.4f}*{progress}"
    x = "iw/2-(iw/zoom/2)"
    y = "ih/2-(ih/zoom/2)"

    if camera:
        sway = max(0.5, duration)
        amount = 0.45 + 0.55 * intensity
        x += f"+{0.012 * amount:.4f}*iw*sin(2*PI*{t}/{sway:.4f})"
        x += f"+{0.010 * amount:.4f}*iw*({progress}-0.5)"
        y += f"+{0.008 * amount:.4f}*ih*cos(2*PI*{t}/{sway:.4f})"
        shake = 0.0025 * amount
        x += (
            f"+{shake:.4f}*iw*(sin(2*PI*0.9*{t})"
            f"+0.6*sin(2*PI*2.3*{t}+1.1))"
        )
        y += (
            f"+{shake * 0.8:.4f}*ih*(cos(2*PI*1.1*{t}+0.7)"
            f"+0.5*sin(2*PI*2.7*{t}))"
        )

    # Alternate the focal point of each punch so consecutive impacts do not all
    # zoom into the exact centre of the frame.
    for index, moment in enumerate(moments):
        sign = 1.0 if index % 2 == 0 else -1.0
        offset = (0.010 + 0.014 * float(moment.strength)) * (0.4 + 0.6 * intensity)
        envelope = (
            f"gte({t},{moment.time:.4f})*"
            f"pow(max(0,1-({t}-{moment.time:.4f})/0.6000),2)"
        )
        x += f"+({offset * sign:.4f})*iw*{envelope}"
        y += f"+({offset * 0.35 * sign:.4f})*ih*{envelope}"

    zoom += "+" + _impact_terms(
        moments,
        t,
        0.55,
        lambda m: (0.030 + 0.050 * float(m.strength)) * (0.35 + 0.65 * intensity),
    )
    zoom = f"min(1.28,max(1.0,{zoom}))"
    x = f"max(0,min(iw-iw/zoom,{x}))"
    y = f"max(0,min(ih-ih/zoom,{y}))"
    return _escape(zoom), _escape(x), _escape(y)


def _flash_expression(
    moments: Sequence[ImpactMoment], intensity: float
) -> str:
    pulse = _impact_terms(
        moments,
        "t",
        0.22,
        lambda m: (0.030 + 0.110 * float(m.strength))
        * (0.35 + 0.65 * intensity),
    )
    return _escape(pulse)


def _build_video_filter(
    width: int,
    height: int,
    fps: float,
    duration: float,
    config: SoloConfig,
    moments: Sequence[ImpactMoment],
    has_intro: bool,
) -> str:
    style = _style(config)
    intensity = _clamp(config.intensity, 0.0, 1.0)
    parts: List[str] = []

    # Fill the frame with a softly blurred, darkened copy of the source, then
    # letterbox-free-fit the sharp source on top.  For matching aspect ratios the
    # background is completely hidden; for vertical reframes it gives a natural
    # cinematic fill instead of black bars.
    parts.append(
        f"[0:v]fps={fps:.4f},split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={max(8, min(width, height) // 40)},"
        f"eq=brightness=-0.17:saturation=0.88[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[base]"
    )
    current = "[base]"

    if config.camera or config.punch:
        zoom, x_expr, y_expr = _camera_expressions(
            moments, fps, duration, intensity, config.camera
        )
        parts.append(
            f"{current}zoompan=z='{zoom}':x='{x_expr}':"
            f"y='{y_expr}':d=1:s={width}x{height}:fps={fps:.4f}[vzoom]"
        )
        current = "[vzoom]"

    # Colour grade.  Applying the flash through ``eq``'s frame-evaluated
    # brightness keeps the effect cheap and avoids an extra overlay input.
    contrast = 1.0 + (style.contrast - 1.0) * (0.55 + 0.85 * intensity)
    saturation = 1.0 + (style.saturation - 1.0) * (0.55 + 0.85 * intensity)
    gamma = 1.0 + (style.gamma - 1.0) * (0.55 + 0.85 * intensity)
    grade = f"eq=contrast={contrast:.4f}:saturation={saturation:.4f}:gamma={gamma:.4f}"
    if config.flash and moments:
        grade += f":brightness='{_flash_expression(moments, intensity)}':eval=frame"
    filters = []
    if style.mono:
        filters.append("hue=s=0")
    filters.append(grade)
    if any(abs(v) > 1e-4 for v in style.balance):
        filters.append(
            f"colorbalance=rs={style.balance[0]:.4f}:"
            f"gs={style.balance[1]:.4f}:bs={style.balance[2]:.4f}"
        )
    parts.append(f"{current}{','.join(filters)}[vcolor]")
    current = "[vcolor]"

    if config.glow and style.glow > 0:
        opacity = _clamp(style.glow * (0.65 + 0.75 * intensity), 0.0, 0.32)
        sigma = max(5, min(width, height) // 34)
        parts.append(
            f"{current}split=2[g0][g1];"
            f"[g1]gblur=sigma={sigma},eq=brightness=0.045:saturation=1.16[glow];"
            f"[g0][glow]blend=all_mode=screen:all_opacity={opacity:.4f}[vglow]"
        )
        current = "[vglow]"

    tail: List[str] = []
    if config.vignette:
        angle = _clamp(style.vignette * (0.65 + 0.65 * intensity), 0.0, 0.95)
        if angle > 0.001:
            tail.append(f"vignette=angle={angle:.4f}")
    if config.grain:
        grain = int(round(3 + 7 * intensity))
        tail.append(f"noise=alls={grain}:allf=t+u")
    if config.letterbox:
        bar = 0.075
        tail.append(
            f"drawbox=x=0:y=0:w=iw:h=ih*{bar:.4f}:color=black:t=fill"
        )
        tail.append(
            f"drawbox=x=0:y=ih*{1.0 - bar:.4f}:w=iw:h=ih*{bar:.4f}:color=black:t=fill"
        )
    fade_in = min(0.5, max(0.0, duration * 0.18))
    fade_out = min(0.9, max(0.0, duration * 0.22))
    if fade_in > 0.01:
        tail.append(f"fade=t=in:st=0:d={fade_in:.4f}")
    if fade_out > 0.01 and duration > fade_out + 0.05:
        tail.append(f"fade=t=out:st={duration - fade_out:.4f}:d={fade_out:.4f}")
    tail.append(f"fps={fps:.4f}")
    tail.append("format=yuv420p")
    parts.append(f"{current}{','.join(tail)}[perf]")

    if has_intro:
        intro = _clamp(config.intro_seconds, 0.3, 8.0)
        fade = min(0.62, max(0.15, intro * 0.28))
        parts.append(
            f"[1:v]scale={width}:{height},setsar=1,fps={fps:.4f},format=yuv420p,"
            f"trim=duration={intro:.4f},"
            f"fade=t=in:st=0:d={fade:.4f},"
            f"fade=t=out:st={max(0.0, intro - fade):.4f}:d={fade:.4f}[intro]"
        )
        parts.append("[intro][perf]concat=n=2:v=1:a=0[vout]")
    else:
        parts.append("[perf]null[vout]")

    return ";".join(parts)


def _build_audio_filter(
    input_index: int,
    total: float,
    intro_seconds: float,
    normalize_audio: bool,
) -> str:
    chain = []
    if intro_seconds > 0.01:
        chain.append(f"adelay={int(round(intro_seconds * 1000))}:all=1")
    if normalize_audio:
        chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    chain.append("apad=pad_dur=1")
    chain.append(f"atrim=0:{total:.4f}")
    if total > 1.0:
        chain.append(f"afade=t=out:st={max(0.0, total - 0.8):.4f}:d=0.8")
    return f"[{input_index}:a]" + ",".join(chain) + "[aout]"


def _wrap_text(draw, text: str, font, max_width: float) -> List[str]:
    lines: List[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else current + " " + word
            if not current or draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
            # A single long word (a URL, a Korean sentence without spaces) still
            # has to fit; break it by character as a last resort.
            if current and draw.textlength(current, font=font) > max_width:
                chunk = ""
                for char in current:
                    if chunk and draw.textlength(chunk + char, font=font) > max_width:
                        lines.append(chunk)
                        chunk = char
                    else:
                        chunk += char
                current = chunk
        lines.append(current)
    return lines or [""]


def render_title_card(
    text: str,
    subtitle: str,
    width: int,
    height: int,
    out_path: str,
    style_key: str = "cinematic",
) -> str:
    """Create a dark, cinematic title card PNG with the accent line."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError as exc:  # pragma: no cover - Pillow is a dependency
        raise PianoSyncError(
            "인트로 타이틀을 그리려면 Pillow가 필요합니다 (pip install Pillow)."
        ) from exc

    text = (text or "").strip()
    subtitle = (subtitle or "").strip()
    if not text and not subtitle:
        raise PianoSyncError("인트로 타이틀에 표시할 글자가 없습니다.")

    font_path, font_index = resolve_font(None)
    if not font_path and subtitle:
        font_path = find_font_path()
    if not font_path:
        raise PianoSyncError(
            "인트로 타이틀에 쓸 글꼴을 찾지 못했습니다. 시스템 글꼴을 설치하거나 "
            "PIANOSYNC_OVERLAY_FONT로 글꼴 파일 경로를 지정해 주세요."
        )

    width = max(2, int(width))
    height = max(2, int(height))
    style = SOLO_STYLES.get(style_key, SOLO_STYLES["cinematic"])

    title_px = max(18, int(round(min(width, height) * 0.088)))
    subtitle_px = max(12, int(round(title_px * 0.38)))
    label_px = max(10, int(round(title_px * 0.20)))

    def load(px: int):
        try:
            return ImageFont.truetype(font_path, px, index=font_index or 0)
        except Exception as exc:  # noqa: BLE001
            raise PianoSyncError(f"인트로 타이틀 글꼴을 불러올 수 없습니다: {font_path}") from exc

    title_font = load(title_px)
    subtitle_font = load(subtitle_px)
    label_font = load(label_px)

    combined = "\n".join(part for part in (text, subtitle) if part)
    missing = missing_hangul(title_font, combined)
    if missing:
        shown = " ".join(missing[:12]) + (" …" if len(missing) > 12 else "")
        raise PianoSyncError(
            f"선택한 글꼴에 한글 글자가 없습니다: {shown} — 한글 글꼴을 사용해 주세요."
        )

    # Vertical gradient background.
    top = np.array([12, 14, 18], dtype=np.float32)
    bottom = np.array([3, 4, 6], dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    background = (top[None, None, :] * (1.0 - ramp) + bottom[None, None, :] * ramp)
    background = np.repeat(background, width, axis=1).astype(np.uint8)
    image = Image.fromarray(background, mode="RGB").convert("RGBA")

    # Soft accent glow behind the title.
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    radius = int(min(width, height) * 0.42)
    cx, cy = width // 2, height // 2
    gdraw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=(style.accent[0], style.accent[1], style.accent[2], 26),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(12, width // 14)))
    image = Image.alpha_composite(image, glow)

    draw = ImageDraw.Draw(image)
    margin = int(width * 0.12)
    max_text_width = width - margin * 2

    title_lines = _wrap_text(draw, text, title_font, max_text_width) if text else []
    subtitle_lines = (
        _wrap_text(draw, subtitle, subtitle_font, max_text_width) if subtitle else []
    )
    line_gap = int(title_px * 0.14)
    title_line_height = int(title_px * 1.24)
    subtitle_line_height = int(subtitle_px * 1.34)
    block_height = (
        len(title_lines) * title_line_height
        + len(subtitle_lines) * subtitle_line_height
        + (line_gap + int(title_px * 0.55) if subtitle_lines else 0)
        + int(title_px * 0.55)
    )
    y = (height - block_height) // 2

    # Small eyebrow label.
    label = "PIANO SOLO"
    label_box = draw.textbbox((0, 0), label, font=label_font)
    label_w = label_box[2] - label_box[0]
    draw.text(
        ((width - label_w) / 2, y),
        label,
        font=label_font,
        fill=(style.accent[0], style.accent[1], style.accent[2], 220),
    )
    y += int(title_px * 0.55)

    # Accent rule.
    rule_w = max(36, int(width * 0.07))
    draw.rectangle(
        [(width - rule_w) // 2, y, (width + rule_w) // 2, y + max(2, title_px // 28)],
        fill=(style.accent[0], style.accent[1], style.accent[2], 255),
    )
    y += int(title_px * 0.38)

    white = (240, 242, 245, 255)
    soft = (174, 181, 190, 235)
    for line in title_lines:
        box = draw.textbbox((0, 0), line, font=title_font)
        draw.text(
            ((width - (box[2] - box[0])) / 2 - box[0], y),
            line,
            font=title_font,
            fill=white,
        )
        y += title_line_height

    if subtitle_lines:
        y += line_gap
        for line in subtitle_lines:
            box = draw.textbbox((0, 0), line, font=subtitle_font)
            draw.text(
                ((width - (box[2] - box[0])) / 2 - box[0], y),
                line,
                font=subtitle_font,
                fill=soft,
            )
            y += subtitle_line_height

    # Footer mark.
    footer = "PIANO SYNC · SOLO CINEMA"
    footer_box = draw.textbbox((0, 0), footer, font=label_font)
    draw.text(
        ((width - (footer_box[2] - footer_box[0])) / 2, height * 0.915),
        footer,
        font=label_font,
        fill=(120, 128, 138, 190),
    )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    image.convert("RGB").save(out_path, format="PNG")
    return out_path


def render_solo(
    media: MediaInfo,
    wav_path: str,
    plan: DramaPlan,
    output: OutputConfig,
    config: SoloConfig,
    moments: Optional[Sequence[ImpactMoment]] = None,
    progress: Optional[Progress] = None,
) -> SoloRenderResult:
    """Render one performance with the dramatic treatment described above."""
    progress = progress or Progress()
    config = config.normalized()
    selected = list(moments if moments is not None else plan.moments)
    # Keep the filter expression bounded; strongest moments are the ones that
    # deserve the punch when the user selected many.
    selected = sorted(selected, key=lambda m: m.strength, reverse=True)[:12]
    selected.sort(key=lambda m: m.time)

    duration = float(media.duration or plan.duration or 0.0)
    if duration <= 0:
        raise PianoSyncError("영상 길이를 확인할 수 없습니다.")
    width, height = resolve_output_size(output, media.width, media.height)
    fps = float(output.fps or media.fps or 30.0)
    intro_seconds = 0.0
    if (config.intro_text or config.intro_subtitle) and config.intro_seconds > 0.05:
        intro_seconds = _clamp(config.intro_seconds, 0.3, 8.0)
    total = duration + intro_seconds

    out_dir = os.path.dirname(os.path.abspath(output.path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg = find_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="pianosync_solo_") as tmp:
        title_path: Optional[str] = None
        if intro_seconds > 0:
            title_path = render_title_card(
                config.intro_text or "Piano Solo",
                config.intro_subtitle,
                width,
                height,
                os.path.join(tmp, "title.png"),
                style_key=config.style,
            )

        filter_complex = _build_video_filter(
            width, height, fps, duration, config, selected, intro_seconds > 0
        )
        if config.include_audio:
            audio_index = 2 if intro_seconds > 0 else 1
            filter_complex += ";" + _build_audio_filter(
                audio_index, total, intro_seconds, config.normalize_audio
            )

        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-i",
            media.path,
        ]
        if title_path:
            cmd += [
                "-loop",
                "1",
                "-framerate",
                f"{fps:.4f}",
                "-t",
                f"{intro_seconds:.4f}",
                "-i",
                title_path,
            ]
        if config.include_audio:
            cmd += ["-i", wav_path]
        cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
        if config.include_audio:
            cmd += ["-map", "[aout]"]
        cmd += [
            "-t",
            f"{total:.4f}",
            "-c:v",
            output.video_codec,
            "-crf",
            str(output.crf),
            "-preset",
            output.preset,
            "-pix_fmt",
            "yuv420p",
        ]
        if config.include_audio:
            cmd += ["-c:a", output.audio_codec, "-b:a", output.audio_bitrate]
        else:
            cmd += ["-an"]
        cmd += ["-movflags", "+faststart", "-y", os.path.abspath(output.path)]

        err_path = os.path.join(tmp, "ffmpeg.log")
        progress.update(0.02, "연출 영상을 렌더링하는 중…")
        run_ffmpeg_with_progress(
            cmd,
            total,
            progress,
            0.02,
            0.98,
            "연출 영상을 렌더링하는 중…",
            err_path,
        )

    progress.update(1.0, "연출 영상 완성")
    return SoloRenderResult(
        path=os.path.abspath(output.path),
        duration=total,
        width=width,
        height=height,
        fps=fps,
        moments=len(selected),
        style=config.style,
        intro_seconds=intro_seconds,
    )
