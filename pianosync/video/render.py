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
from typing import List, Optional

import numpy as np

from ..audio.extract import MediaInfo, find_ffmpeg, load_wav
from ..config import OutputConfig, TransitionConfig
from ..core.alignment import AlignmentResult
from ..core.progress import Progress
from ..core.transitions import TransitionPlan
from ..errors import CancelledError, MediaError, PianoSyncError
from .audiomix import assemble_audio
from .mapping import SegmentTiming, plan_segment_timings, resolve_output_size


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
    chain.append(f"fps={fps}")
    if effect == "fadeblack" and fade > 0:
        if not is_first:
            chain.append(f"fade=t=in:st=0:d={fade:.4f}")
        if not is_last:
            chain.append(f"fade=t=out:st={max(0.0, timing.out_duration - fade):.4f}:d={fade:.4f}")
    chain.append("format=yuv420p")
    return f"[{input_index}:v]" + ",".join(chain) + output_label


def _build_filter_complex(
    timings: List[SegmentTiming],
    media_a: MediaInfo,
    media_b: MediaInfo,
    width: int,
    height: int,
    fps: float,
    effect: str,
    fade: float,
) -> str:
    parts: List[str] = []
    labels: List[str] = []
    for n, timing in enumerate(timings):
        input_index = 0 if timing.source == "A" else 1
        source_duration = media_a.duration if timing.source == "A" else media_b.duration
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
    elif effect == "crossfade" and fade > 0:
        accumulated = timings[0].out_duration
        current = labels[0]
        for n in range(1, len(timings)):
            offset = max(0.0, accumulated - fade)
            out_label = "[vout]" if n == len(timings) - 1 else f"[x{n}]"
            parts.append(
                f"{current}{labels[n]}xfade=transition=fade:duration={fade:.4f}:"
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
    alignment: AlignmentResult,
    media_a: MediaInfo,
    media_b: MediaInfo,
    wav_a: str,
    wav_b: str,
    output: OutputConfig,
    transition_cfg: TransitionConfig,
    progress: Optional[Progress] = None,
) -> RenderResult:
    progress = progress or Progress()
    ffmpeg = find_ffmpeg()

    timings = plan_segment_timings(plan.segments, b_duration=media_b.duration or alignment.b_duration)
    if not timings:
        raise PianoSyncError("There is nothing to render (no segments).")

    width, height = resolve_output_size(output, media_a.width, media_a.height)
    fps = output.fps or media_a.fps or 30.0
    effect = transition_cfg.effect if transition_cfg.effect in ("cut", "crossfade", "fadeblack") else "cut"
    fade = _clamp_fade(transition_cfg.effect_duration, timings, effect)

    total_video = sum(t.out_duration for t in timings)
    if effect == "crossfade":
        total_video -= max(0, len(timings) - 1) * fade

    out_dir = os.path.dirname(os.path.abspath(output.path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pianosync_render_") as tmp:
        video_only = os.path.join(tmp, "video_only.mp4")
        err_path = os.path.join(tmp, "ffmpeg.log")
        filter_complex = _build_filter_complex(
            timings, media_a, media_b, width, height, fps, effect, fade
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
            media_a.path,
            "-i",
            media_b.path,
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
            cmd,
            total_video,
            progress,
            0.05,
            0.6,
            "Rendering video…",
            err_path,
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
        a_signal, a_sr = load_wav(wav_a)
        b_signal, b_sr = load_wav(wav_b)
        audio, sample_rate = assemble_audio(
            plan.segments,
            alignment.time_map,
            a_signal,
            a_sr,
            b_signal,
            b_sr,
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
