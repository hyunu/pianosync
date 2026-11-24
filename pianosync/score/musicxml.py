"""Minimal MusicXML (.musicxml / .mxl) reader for score playback."""

from __future__ import annotations

import os
import zipfile
from typing import List
from xml.etree import ElementTree as ET

from ..errors import PianoSyncError
from .model import ScoreDocument, ScoreNote

_STEP_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACCIDENTALS = {
    "sharp": "#",
    "flat": "b",
    "natural": "",
    "double-sharp": "##",
    "flat-flat": "bb",
}


def _read_xml(path: str) -> bytes:
    if not os.path.isfile(path):
        raise PianoSyncError(f"악보 파일을 찾을 수 없습니다: {path}")
    with open(path, "rb") as handle:
        raw = handle.read()
    if not raw.lstrip().startswith(b"PK"):
        return raw
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                rootfile = container.find(".//rootfile")
                name = rootfile.get("full-path") if rootfile is not None else None
            except (KeyError, ET.ParseError):
                name = None
            if not name:
                candidates = [
                    n for n in archive.namelist()
                    if n.lower().endswith(".xml") and not n.startswith("META-INF/")
                ]
                if not candidates:
                    raise PianoSyncError("압축 악보 안에 XML 파일이 없습니다.")
                name = candidates[0]
            return archive.read(name)
    except zipfile.BadZipFile as exc:
        raise PianoSyncError(f"악보 파일을 읽을 수 없습니다: {exc}") from exc


def _title(root: ET.Element) -> str:
    for path in ("work/work-title", "movement-title", "credit/credit-words"):
        text = root.findtext(path)
        if text and text.strip():
            return " ".join(text.split())
    return ""


def _pitch(note: ET.Element) -> tuple:
    pitch = note.find("pitch")
    if pitch is None:
        return 0, ""
    step = (pitch.findtext("step") or "").strip().upper()
    if step not in _STEP_SEMITONES:
        raise PianoSyncError("지원하지 않는 음표입니다 (step을 읽을 수 없습니다).")
    try:
        alter = int(float(pitch.findtext("alter") or 0))
        octave = int(float(pitch.findtext("octave") or 0))
    except ValueError as exc:
        raise PianoSyncError("음표의 alter/octave 값을 읽을 수 없습니다.") from exc
    midi = (octave + 1) * 12 + _STEP_SEMITONES[step] + alter
    explicit = (note.findtext("accidental") or "").strip()
    accidental = _ACCIDENTALS.get(explicit, "")
    if not accidental and alter:
        accidental = "#" if alter > 0 else "b"
    return midi, accidental


def parse_musicxml(path: str) -> ScoreDocument:
    """Parse a MusicXML file into absolute-time note events."""
    root = ET.fromstring(_read_xml(path))
    warnings: List[str] = []
    notes: List[ScoreNote] = []

    parts = root.findall("part")
    if not parts:
        raise PianoSyncError("악보에 part가 없습니다.")

    for part_index, part in enumerate(parts):
        divisions = 1
        tempo = 120.0
        beat = 0.0
        sec = 0.0
        last_onset_beat = 0.0
        last_onset_sec = 0.0
        default_staff = part_index + 1

        for measure_index, measure in enumerate(part.findall("measure"), start=1):
            for child in list(measure):
                if child.tag == "attributes":
                    value = child.findtext("divisions")
                    if value:
                        try:
                            divisions = max(1, int(float(value)))
                        except ValueError:
                            pass
                    continue
                if child.tag == "direction":
                    sound = child.find("sound")
                    if sound is not None and sound.get("tempo"):
                        try:
                            tempo = max(20.0, min(300.0, float(sound.get("tempo"))))
                        except (TypeError, ValueError):
                            pass
                    continue
                if child.tag != "note":
                    continue

                try:
                    duration_div = int(float(child.findtext("duration") or 0))
                except ValueError:
                    duration_div = 0
                duration_beats = max(0.0, duration_div / float(divisions))
                is_chord = child.find("chord") is not None
                if is_chord:
                    onset_beat = last_onset_beat
                    onset_sec = last_onset_sec
                else:
                    onset_beat = beat
                    onset_sec = sec
                    last_onset_beat = beat
                    last_onset_sec = sec
                    beat += duration_beats
                    sec += duration_beats * (60.0 / tempo)

                is_rest = child.find("rest") is not None
                midi, accidental = (0, "") if is_rest else _pitch(child)
                tie = ""
                if any(t.get("type") == "start" for t in child.findall("tie")):
                    tie = "start"
                elif any(t.get("type") == "stop" for t in child.findall("tie")):
                    tie = "stop"
                try:
                    staff = int(float(child.findtext("staff") or default_staff))
                except ValueError:
                    staff = default_staff
                try:
                    voice = int(float(child.findtext("voice") or 1))
                except ValueError:
                    voice = 1

                notes.append(
                    ScoreNote(
                        midi=midi,
                        start_beat=onset_beat,
                        duration_beats=duration_beats,
                        start_sec=onset_sec,
                        duration_sec=duration_beats * (60.0 / tempo),
                        measure=measure_index,
                        staff=staff,
                        voice=voice,
                        accidental=accidental,
                        is_rest=is_rest,
                        tie=tie,
                    )
                )

    if not notes:
        raise PianoSyncError("악보에서 음표를 찾을 수 없습니다.")

    notes.sort(key=lambda n: (n.start_sec, n.staff, n.midi, n.measure))
    duration = max(float(n.end_sec) for n in notes)

    if any(n.tie for n in notes):
        warnings.append("이음줄(tie) 음표는 각각 별도 음표로 표시합니다.")
    return ScoreDocument(
        title=_title(root),
        notes=notes,
        duration_sec=duration,
        source=os.path.basename(path),
        warnings=warnings,
    )
