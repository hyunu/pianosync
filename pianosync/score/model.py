"""Data structures shared by the score parsers, alignment and renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ScoreNote:
    """One note (or rest) from an uploaded score."""

    midi: int
    start_beat: float
    duration_beats: float
    start_sec: float
    duration_sec: float
    measure: int
    staff: int = 1
    voice: int = 1
    accidental: str = ""
    is_rest: bool = False
    tie: str = ""

    @property
    def end_sec(self) -> float:
        return float(self.start_sec) + max(0.0, float(self.duration_sec))

    def to_dict(self, index: int) -> Dict[str, Any]:
        return {
            "index": index,
            "midi": int(self.midi),
            "measure": int(self.measure),
            "staff": int(self.staff),
            "voice": int(self.voice),
            "accidental": self.accidental,
            "is_rest": bool(self.is_rest),
            "tie": self.tie,
            "score_time": round(float(self.start_sec), 3),
            "duration": round(float(self.duration_sec), 3),
        }


@dataclass
class ScoreDocument:
    """A parsed score with absolute timing already resolved."""

    title: str
    notes: List[ScoreNote]
    duration_sec: float
    source: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def playable_notes(self) -> List[ScoreNote]:
        return [note for note in self.notes if not note.is_rest and note.midi > 0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "note_count": len(self.notes),
            "playable_count": len(self.playable_notes),
            "duration": round(float(self.duration_sec), 3),
            "measures": max((int(n.measure) for n in self.notes), default=0),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class NoteTiming:
    """A single note placed on the real performance timeline."""

    index: int
    note: ScoreNote
    score_time: float
    video_time: float
    video_end: float

    def to_dict(self) -> Dict[str, Any]:
        data = self.note.to_dict(self.index)
        data.update(
            {
                "video_time": round(float(self.video_time), 3),
                "video_end": round(float(self.video_end), 3),
            }
        )
        return data


@dataclass
class ScoreSync:
    """Alignment result consumed by the web preview and MP4 overlay."""

    events: List[NoteTiming]
    confidence: float = 0.0
    low_confidence: List[Tuple[float, float]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    time_map: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_count": len(self.events),
            "confidence": round(float(self.confidence), 3),
            "low_confidence": [
                [round(float(a), 3), round(float(b), 3)] for a, b in self.low_confidence
            ],
            "warnings": list(self.warnings),
        }
