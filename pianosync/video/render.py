"""Final video rendering with FFmpeg.

The video is built from normalized (scaled, fps-converted) clips concatenated
with the selected effect, rendered to a temporary video-only file, and then
muxed with the assembled audio track. Duration is preserved for ``cut`` and
``fadeblack`` and reduced by the overlap for ``crossfade``, matching the audio
assembly so picture and sound stay in sync.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from ..audio.extract import MediaInfo, find_ffmpeg, load_wav
from ..config import (
    DIP_COLORS,
    DIP_EFFECTS,
    OutputConfig,
    TransitionConfig,
    XFADE_EFFECTS,
    XFADE_TRANSITIONS,
    normalize_effect,
)
from ..core.alignment import AlignmentResult, TimeMap
from ..core.progress import Progress
from ..core.transitions import TransitionPlan
from ..errors import CancelledError, MediaError, PianoSyncError
from .audiomix import assemble_audio_multi
from .mapping import (
    SegmentTiming,
    plan_segment_timings_multi,
    resolve_output_size,
)


@dataclass
class RenderTrack:
    """A performance prepared for rendering."""

    media: MediaInfo
    wav_path: str
    time_map: Optional[TimeMap] = None  # reference -> this track (None for reference)


@dataclass
class RenderResult:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    segments: int
    audio_mode: str
    audio_included: bool


def _clamp_fade(
    effect_duration: float, timings: List[SegmentTiming], effect: str
) -> float:
    if effect == "cut" or len(timings) < 2:
        return 0.0
    min_dur = min(t.out_duration for t in timings)
    return max(0.0, min(effect_duration, min_dur * 0.4))


def _segment_filter(
    timing: SegmentTiming,
    input_index: int,
    width: int,
    height: int,
    fps: float,
    source_duration: float,
    output_label: str,
    effect: str,
    fade: float,
    is_first: bool,
    is_last: bool,
) -> str:
    src_start = timing.src_start
    remaining = max(0.0, source_duration - src_start) if source_duration else timing.out_duration
    keep = min(timing.out_duration, remaining) if source_duration else timing.out_duration
    pad = max(0.0, timing.out_duration - keep)

    chain = [
        f"trim=start={src_start:.6f}:duration={keep:.6f}",
        "setpts=PTS-STARTPTS",
    ]
    if pad > 1e-4:
        chain.append(f"tpad=stop_mode=clone:stop_duration={pad:.6f}")
    chain.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    chain.append("setsar=1")
    if effect in DIP_EFFECTS and fade > 0:
        color = DIP_COLORS.get(effect, "black")
        if not is_first:
            chain.append(f"fade=t=in:st=0:d={fade:.6f}:color={color}")
        if not is_last:
            out_start = max(0.0, timing.out_duration - fade)
            chain.append(f"fade=t=out:st={out_start:.6f}:d={fade:.6f}:color={color}")
    chain.append(f"fps={fps}")
    chain.append("format=yuv420p")
    return f"[{input_index}:v]" + ",".join(chain) + output_label


def _build_filter_complex(
    timings: List[SegmentTiming],
    source_durations: Sequence[float],
    width: int,
    height: int,
    fps: float,
    effect: str,
    fade: float,
) -> str:
    parts: List[str] = []
    labels: List[str] = []
    for n, timing in enumerate(timings):
        input_index = int(getattr(timing, "input_index", 0) or 0)
        source_duration = (
            source_durations[input_index] if 0 <= input_index < len(source_durations) else 0.0
        )
        label = f"[s{n}]"
        parts.append(
            _segment_filter(
                timing,
                input_index,
                width,
                height,
                fps,
                source_duration,
                label,
                effect,
                fade,
                is_first=(n == 0),
                is_last=(n == len(timings) - 1),
            )
        )
        labels.append(label)

    if len(timings) == 1:
        parts.append(f"{labels[0]}null[vout]")
    elif effect in XFADE_EFFECTS and fade > 0:
        transition = XFADE_TRANSITIONS.get(effect, "fade")
        accumulated = timings[0].out_duration
        current = labels[0]
        for n in range(1, len(timings)):
            offset = max(0.0, accumulated - fade)
            out_label = "[vout]" if n == len(timings) - 1 else f"[x{n}]"
            parts.append(
                f"{current}{labels[n]}xfade=transition={transition}:duration={fade:.6f}:"
                f"offset={offset:.6f}{out_label}"
            )
            accumulated = accumulated + timings[n].out_duration - fade
            current = out_label
    else:
        parts.append(
            "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[vout]"
        )
    return ";".join(parts)


def _run_ffmpeg_with_progress(
    cmd: List[str],
    total_duration: float,
    progress: Progress,
    base: float,
    span: float,
    message: str,
    stderr_path: str,
) -> None:
    progress.update(base, message)
    with open(stderr_path, "wb") as err_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=err_file,
            stdin=subprocess.DEVNULL,
        )
        try:
            while True:
                if progress.is_cancelled and progress.is_cancelled():
                    process.kill()
                    raise CancelledError("Rendering cancelled")
                line = process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text.startswith("out_time_ms="):
                    try:
                        seconds = float(text.split("=", 1)[1]) / 1_000_000.0
                    except ValueError:
                        continue
                    frac = 1.0 if total_duration <= 0 else seconds / total_duration
                    progress.update(base + span * min(max(frac, 0.0), 1.0), message)
            process.wait()
        finally:
            if process.stdout:
                process.stdout.close()
    if process.returncode != 0:
        with open(stderr_path, "rb") as handle:
            detail = handle.read().decode("utf-8", "replace").strip()
        detail = detail[-1500:]
        raise MediaError(f"{message} failed.\n{detail}")


def render_final_video(
    plan: TransitionPlan,
    alignment: Optional[AlignmentResult],
    media_a: MediaInfo,
    media_b: MediaInfo,
    wav_a: str,
    wav_b: str,
    output: OutputConfig,
    transition_cfg: TransitionConfig,
    progress: Optional[Progress] = None,
) -> RenderResult:
    """Two-video compatibility wrapper around :func:`render_final_video_multi`."""
    tracks = [
        RenderTrack(media=media_a, wav_path=wav_a, time_map=None),
        RenderTrack(
            media=media_b,
            wav_path=wav_b,
            time_map=alignment.time_map if alignment is not None else None,
        ),
    ]
    return render_final_video_multi(plan, tracks, output, transition_cfg, progress=progress)


def render_final_video_multi(
    plan: TransitionPlan,
    tracks: Sequence[RenderTrack],
    output: OutputConfig,
    transition_cfg: TransitionConfig,
    progress: Optional[Progress] = None,
) -> RenderResult:
    progress = progress or Progress()
    ffmpeg = find_ffmpeg()
    tracks = list(tracks)
    if not tracks:
        raise PianoSyncError("There is nothing to render (no inputs).")

    source_durations = [float(t.media.duration or 0.0) for t in tracks]
    timings = plan_segment_timings_multi(plan.segments, source_durations)
    if not timings:
        raise PianoSyncError("There is nothing to render (no segments).")

    ref_media = tracks[0].media
    width, height = resolve_output_size(output, ref_media.width, ref_media.height)
    fps = output.fps or ref_media.fps or 30.0
    effect = normalize_effect(transition_cfg.effect)
    fade = _clamp_fade(transition_cfg.effect_duration, timings, effect)

    total_video = sum(t.out_duration for t in timings)
    if effect in XFADE_EFFECTS:
        total_video -= max(0, len(timings) - 1) * fade

    out_dir = os.path.dirname(os.path.abspath(output.path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pianosync_render_") as tmp:
        video_only = os.path.join(tmp, "video_only.mp4")
        err_path = os.path.join(tmp, "ffmpeg.log")
        filter_complex = _build_filter_complex(
            timings, source_durations, width, height, fps, effect, fade
        )
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
        for track in tracks:
            cmd += ["-i", track.media.path]
        cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            output.video_codec,
            "-crf",
            str(output.crf),
            "-preset",
            output.preset,
            "-pix_fmt",
            "yuv420p",
            "-y",
            video_only,
        ]
        _run_ffmpeg_with_progress(
            cmd, total_video, progress, 0.05, 0.6, "Rendering video…", err_path
        )

        include_audio = output.include_audio and transition_cfg.audio_mode != "none"
        if not include_audio:
            final_path = os.path.abspath(output.path)
            if os.path.exists(final_path):
                os.remove(final_path)
            os.replace(video_only, final_path)
            progress.update(1.0, "Render complete")
            return RenderResult(
                path=final_path,
                duration=total_video,
                width=width,
                height=height,
                fps=fps,
                segments=len(timings),
                audio_mode=transition_cfg.audio_mode,
                audio_included=False,
            )

        progress.update(0.68, "Assembling audio…")
        track_signals = []
        for track in tracks:
            signal, sample_rate = load_wav(track.wav_path)
            track_signals.append((signal, sample_rate, track.time_map))
        audio, sample_rate = assemble_audio_multi(
            plan.segments,
            track_signals,
            audio_mode=transition_cfg.audio_mode,
            effect=effect,
            effect_duration=fade,
        )
        audio_path = os.path.join(tmp, "audio.wav")
        _write_wav(audio_path, audio, sample_rate)

        progress.update(0.75, "Muxing audio and video…")
        mux_err = os.path.join(tmp, "mux.log")
        final_path = os.path.abspath(output.path)
        mux_cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-progress",
            "pipe:1",
            "-nostats",
            "-i",
            video_only,
            "-i",
            audio_path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            output.audio_codec,
            "-b:a",
            output.audio_bitrate,
            "-shortest",
            "-y",
            final_path,
        ]
        _run_ffmpeg_with_progress(
            mux_cmd, total_video, progress, 0.75, 0.25, "Muxing audio and video…", mux_err
        )

    progress.update(1.0, "Render complete")
    return RenderResult(
        path=final_path,
        duration=total_video,
        width=width,
        height=height,
        fps=fps,
        segments=len(timings),
        audio_mode=transition_cfg.audio_mode,
        audio_included=True,
    )


def _write_wav(path: str, samples: np.ndarray, sample_rate: int) -> None:
    from scipy.io import wavfile

    data = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak
    pcm = np.clip(data * 32767.0, -32768, 32767).astype(np.int16)
    wavfile.write(path, sample_rate, pcm)
