"""Small MIDI (format 0/1) reader for score playback (no external deps)."""

from __future__ import annotations

import os
import struct
from typing import List, Tuple

from ..errors import PianoSyncError
from .model import ScoreDocument, ScoreNote

_ACCIDENTAL_PCS = {1, 3, 6, 8, 10}


def _vlq(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise PianoSyncError("MIDI 파일의 가변 길이 값이 잘못되었습니다.")


def parse_midi(path: str) -> ScoreDocument:
    """Parse note-on/off events from a MIDI file into absolute seconds."""
    if not os.path.isfile(path):
        raise PianoSyncError(f"악보 파일을 찾을 수 없습니다: {path}")
    with open(path, "rb") as handle:
        data = handle.read()
    if len(data) < 14 or data[:4] != b"MThd":
        raise PianoSyncError("MIDI 파일이 아닙니다.")

    _, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise PianoSyncError("SMPTE 시간 기준 MIDI는 지원하지 않습니다.")

    pos = 14
    tempo_events: List[Tuple[int, float]] = [(0, 500000.0)]
    pending: dict = {}
    notes: List[Tuple[int, int, int, int]] = []  # start_tick, end_tick, midi, track
    title = ""
    found_track = False

    for track_index in range(ntracks):
        if pos + 8 > len(data) or data[pos:pos + 4] != b"MTrk":
            raise PianoSyncError("MIDI 트랙을 읽을 수 없습니다.")
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        pos += 8
        end = min(len(data), pos + length)
        tick = 0
        running = None

        while pos < end:
            delta, pos = _vlq(data, pos)
            tick += delta
            if pos >= end:
                break
            status = data[pos]
            if status & 0x80:
                pos += 1
                running = status
            elif running is None:
                raise PianoSyncError("MIDI running status를 해석할 수 없습니다.")
            else:
                status = running

            if status == 0xFF:
                if pos >= end:
                    break
                meta_type = data[pos]
                pos += 1
                size, pos = _vlq(data, pos)
                payload = data[pos:pos + size]
                pos += size
                if meta_type == 0x51 and len(payload) == 3:
                    tempo_events.append((tick, int.from_bytes(payload, "big")))
                elif meta_type == 0x03 and not title and payload:
                    title = payload.decode("utf-8", "replace").strip()
                elif meta_type == 0x2F:
                    break
            elif status in (0xF0, 0xF7):
                size, pos = _vlq(data, pos)
                pos += size
            else:
                kind = status & 0xF0
                if kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    if pos + 2 > end:
                        break
                    first, second = data[pos], data[pos + 1]
                    pos += 2
                    if kind == 0x90 and second > 0:
                        pending[(track_index, status & 0x0F, first)] = tick
                    elif kind == 0x80 or (kind == 0x90 and second == 0):
                        key = (track_index, status & 0x0F, first)
                        if key in pending:
                            notes.append((pending.pop(key), tick, first, track_index))
                elif kind in (0xC0, 0xD0):
                    pos += 1

        found_track = True
        pos = end

    if not found_track or not notes:
        raise PianoSyncError("MIDI 파일에서 음표를 찾을 수 없습니다.")

    tempo_events.sort(key=lambda item: item[0])

    def seconds_at(tick_value: int) -> float:
        seconds = 0.0
        previous_tick = 0
        tempo = tempo_events[0][1]
        for event_tick, event_tempo in tempo_events[1:]:
            if event_tick > tick_value:
                break
            seconds += (event_tick - previous_tick) / division * (tempo / 1_000_000.0)
            previous_tick = event_tick
            tempo = event_tempo
        seconds += (tick_value - previous_tick) / division * (tempo / 1_000_000.0)
        return max(0.0, seconds)

    result: List[ScoreNote] = []
    for start_tick, end_tick, midi, track_index in notes:
        start_sec = seconds_at(start_tick)
        end_sec = seconds_at(max(end_tick, start_tick))
        start_beat = start_tick / division
        result.append(
            ScoreNote(
                midi=midi,
                start_beat=start_beat,
                duration_beats=max(0.0, (end_tick - start_tick) / division),
                start_sec=start_sec,
                duration_sec=max(0.01, end_sec - start_sec),
                measure=int(start_beat // 4) + 1,
                staff=1,
                voice=1,
                accidental="#" if midi % 12 in _ACCIDENTAL_PCS else "",
                is_rest=False,
                tie="",
            )
        )

    result.sort(key=lambda n: (n.start_sec, n.midi))
    duration = max(float(n.end_sec) for n in result)
    return ScoreDocument(
        title=title or os.path.splitext(os.path.basename(path))[0],
        notes=result,
        duration_sec=duration,
        source=os.path.basename(path),
        warnings=[],
    )
