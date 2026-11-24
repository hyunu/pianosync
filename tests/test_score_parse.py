"""MusicXML / MIDI parsing tests for the score-play feature."""

from __future__ import annotations

import os
import struct

import pytest

from pianosync.score.midi import parse_midi
from pianosync.score.musicxml import parse_musicxml

MUSICXML = """<?xml version="1.0" encoding="utf-8"?>
<score-partwise version="3.1">
  <work><work-title>테스트 곡</work-title></work>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>2</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type></note>
      <note><pitch><step>E</step><alter>1</alter><octave>4</octave></pitch><duration>2</duration><type>quarter</type><accidental>sharp</accidental></note>
      <note><rest/><duration>4</duration><type>half</type></note>
    </measure>
    <measure number="2">
      <direction placement="above">
        <direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>60</per-minute></metronome></direction-type>
        <sound tempo="60"/>
      </direction>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><type>half</type></note>
      <note><chord/><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><type>half</type></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type><tie type="start"/></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>2</duration><type>quarter</type><tie type="stop"/></note>
    </measure>
  </part>
</score-partwise>
"""


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _midi_bytes() -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
    events = [
        _vlq(0) + b"\xff\x51\x03" + (500000).to_bytes(3, "big"),
        _vlq(0) + b"\x90\x3c\x64",
        _vlq(480) + b"\x80\x3c\x00",
        _vlq(0) + b"\x90\x40\x64",
        _vlq(240) + b"\x80\x40\x00",
        _vlq(0) + b"\xff\x2f\x00",
    ]
    track = b"".join(events)
    return header + b"MTrk" + struct.pack(">I", len(track)) + track


def test_musicxml_parses_notes_tempo_chords_and_ties(tmp_path):
    path = tmp_path / "score.musicxml"
    path.write_text(MUSICXML, encoding="utf-8")

    doc = parse_musicxml(str(path))

    assert doc.title == "테스트 곡"
    assert len(doc.notes) == 7
    first = doc.notes[0]
    assert (first.midi, first.measure, first.staff) == (60, 1, 1)
    assert first.start_sec == pytest.approx(0.0)
    assert first.duration_sec == pytest.approx(0.5)
    assert doc.notes[1].midi == 65
    assert doc.notes[1].accidental == "#"
    assert doc.notes[1].start_sec == pytest.approx(0.5)
    assert doc.notes[2].is_rest is True
    assert doc.notes[2].midi == 0
    # 60 BPM from measure 2: a half note is two seconds.
    assert doc.notes[3].midi == 67
    assert doc.notes[3].start_sec == pytest.approx(2.0)
    assert doc.notes[3].duration_sec == pytest.approx(2.0)
    # Chord shares the onset but keeps its own pitch.
    assert doc.notes[4].midi == 72
    assert doc.notes[4].start_sec == pytest.approx(2.0)
    assert doc.notes[5].tie == "start"
    assert doc.notes[6].tie == "stop"
    assert doc.duration_sec == pytest.approx(6.0)


def test_musicxml_rejects_unknown_files(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("not a score", encoding="utf-8")
    with pytest.raises(Exception):
        parse_musicxml(str(path))


def test_midi_parses_note_events(tmp_path):
    path = tmp_path / "score.mid"
    path.write_bytes(_midi_bytes())

    doc = parse_midi(str(path))

    assert len(doc.notes) == 2
    assert doc.notes[0].midi == 60
    assert doc.notes[0].start_sec == pytest.approx(0.0)
    assert doc.notes[0].duration_sec == pytest.approx(0.5)
    assert doc.notes[1].midi == 64
    assert doc.notes[1].start_sec == pytest.approx(0.5)
    assert doc.notes[1].duration_sec == pytest.approx(0.25)
    assert doc.duration_sec == pytest.approx(0.75)
    assert os.path.basename(doc.source) == "score.mid"
