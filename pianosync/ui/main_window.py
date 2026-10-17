"""Main application window with the five functional areas."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..ai.gemini import GeminiClient
from ..audio.extract import probe_media
from ..config import AlignmentConfig, OutputConfig, TransitionConfig
from ..core.analysis import AnalysisEngine
from ..core.progress import Progress
from ..core.transitions import TransitionPlan, generate_transitions
from ..errors import MediaError, ProjectError
from ..project.store import Project, load_project, save_project
from ..ui import tasks
from ..ui.theme import SPACE, TOKENS
from ..ui.waveform import WaveformWidget
from ..ui.workers import TaskWorker, friendly_error, run_task
from ..video.frames import extract_frame_png

CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".pianosync", "cache")

QUALITY_MAP = {"빠르게": "fast", "균형": "balanced", "정확하게": "accurate"}
MODE_MAP = {"음악 위치 기준 (모드 1)": "musical", "출력 시간 기준 (모드 2)": "fixed_output"}
EFFECT_MAP = {
    "하드 컷": "cut",
    "크로스페이드": "crossfade",
    "블랙 페이드": "fadeblack",
    "화이트 페이드": "fadewhite",
    "그레이 페이드": "fadegrays",
    "디졸브": "dissolve",
    "좌측 슬라이드": "slideleft",
    "우측 슬라이드": "slideright",
    "원형 열기": "circleopen",
}
AUDIO_MAP = {"영상 A": "A", "영상 B": "B", "A/B 정렬 전환": "aligned", "오디오 없음": "none"}
START_MAP = {"영상 A": "A", "영상 B": "B"}
PRESET_MAP = {"ultrafast": "ultrafast", "veryfast": "veryfast", "fast": "fast", "medium": "medium", "slow": "slow"}


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:05.2f}"


class _PreviewPane(QWidget):
    """Shows the current A/B frames at a musical position."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*[SPACE["4"]] * 4)
        layout.setSpacing(SPACE["4"])
        self.a_path = ""
        self.b_path = ""
        self.time_map = None

        frames = QHBoxLayout()
        frames.setSpacing(SPACE["4"])
        self.label_a = QLabel("A 프레임")
        self.label_b = QLabel("B 프레임")
        self.label_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_a.setMinimumSize(420, 220)
        self.label_b.setMinimumSize(420, 220)
        self.label_a.setProperty("class", "plate")
        self.label_b.setProperty("class", "plate")
        frames.addWidget(self.label_a)
        frames.addWidget(self.label_b)
        layout.addLayout(frames)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.time_label = QLabel("A 0:00.00 / B 0:00.00")
        self.time_label.setProperty("class", "fig")
        row = QHBoxLayout()
        self.btn_prev = QPushButton("◀ 이전 전환")
        self.btn_next = QPushButton("다음 전환 ▶")
        self.btn_refresh = QPushButton("프레임 새로고침")
        row.addWidget(self.btn_prev)
        row.addWidget(self.btn_next)
        row.addWidget(self.btn_refresh)
        layout.addWidget(self.slider)
        layout.addWidget(self.time_label)
        layout.addLayout(row)

        self.current_segment_index = -1
        self.segments: List[dict] = []
        self._time_seconds = 0.0

        self.current_segment_index = -1
        self.segments: List[dict] = []
        self._time_seconds = 0.0

    def set_sources(self, a_path: str, b_path: str, alignment):
        self.a_path = a_path
        self.b_path = b_path
        self.time_map = alignment.time_map if alignment else None
        duration = max(alignment.feat_a.times[-1], 0.001) if alignment else 1.0
        self.slider.setEnabled(alignment is not None)

    def set_segments(self, plan: Optional[TransitionPlan]):
        self.segments = [s.__dict__ for s in plan.segments] if plan else []

    def show_time(self, a_time: float):
        if self.time_map is None:
            return
        self._time_seconds = max(0.0, min(a_time, self._time_seconds_bound()))
        b_time = self.time_map.b_for_a(self._time_seconds)
        self.time_label.setText(f"A {_fmt_time(self._time_seconds)} / B {_fmt_time(b_time)}")

    def _time_seconds_bound(self) -> float:
        return float(self.time_map.a_times[-1]) if self.time_map is not None and self.time_map.a_times.size else 0.0

    def refresh_frames(self, width: int = 320):
        if not self.a_path or self.time_map is None:
            return
        b_time = self.time_map.b_for_a(self._time_seconds)
        self.label_a.setText("불러오는 중…")
        self.label_b.setText("불러오는 중…")
        run_task(
            self,
            _extract_both_frames,
            lambda result: self._apply_frames(result),
            on_error=lambda text: QMessageBox.warning(self, "미리보기", text),
            path_a=self.a_path,
            path_b=self.b_path,
            a_time=self._time_seconds,
            b_time=b_time,
            width=width,
        )

    def _apply_frames(self, result):
        png_a, png_b = result
        if png_a:
            pix_a = QPixmap()
            pix_a.loadFromData(png_a)
            self.label_a.setPixmap(pix_a.scaledToWidth(max(1, self.label_a.width() - 4), Qt.TransformationMode.SmoothTransformation))
            self.label_a.setText("")
        else:
            self.label_a.setText("프레임 없음")
        if png_b:
            pix_b = QPixmap()
            pix_b.loadFromData(png_b)
            self.label_b.setPixmap(pix_b.scaledToWidth(max(1, self.label_b.width() - 4), Qt.TransformationMode.SmoothTransformation))
            self.label_b.setText("")
        else:
            self.label_b.setText("프레임 없음")

    def go_to_segment(self, index: Optional[int]):
        if not self.segments or index is None:
            return
        self.current_segment_index = index % len(self.segments)
        seg = self.segments[self.current_segment_index]
        self.show_time(float(seg["a_start"]))
        self.refresh_frames()


def _extract_both_frames(path_a, path_b, a_time, b_time, width, progress=None):
    return (
        extract_frame_png(path_a, a_time, width),
        extract_frame_png(path_b, b_time, width),
    )


class MainWindow(QMainWindow):
    def __init__(self, app_config=None):
        super().__init__()
        self.setWindowTitle("Piano Sync Switcher — 두 연주 비교 영상 생성")
        self.resize(1180, 860)
        os.makedirs(CACHE_ROOT, exist_ok=True)
        self.engine = AnalysisEngine(CACHE_ROOT)
        self.project = Project(engine=self.engine)
        self.env_a: Optional[Tuple[List, List]] = None
        self.env_b: Optional[Tuple[List, List]] = None
        self.ai_client = GeminiClient()
        self._current_worker: Optional[TaskWorker] = None

        self._build_ui()
        self._sync_from_project()

        self.setMinimumSize(980, 680)
        self.resize(1400, 900)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        tabs = QTabWidget()
        tabs.addTab(self._build_input_tab(), "1. 입력 및 분석")
        tabs.addTab(self._build_edit_tab(), "2. 정렬 편집")
        tabs.addTab(self._build_preview_tab(), "3. 미리보기")
        tabs.addTab(self._build_output_tab(), "4. 출력")
        self.setCentralWidget(tabs)
        self.tabs = tabs

        menu = self.menuBar()
        file_menu = menu.addMenu("파일")
        file_menu.addAction("새 프로젝트", self.new_project)
        file_menu.addAction("열기…", self.open_project)
        file_menu.addAction("저장…", self.save_project_as)
        file_menu.addAction("저장 (현재 파일)", self.save_project_current)
        tools_menu = menu.addMenu("도구")
        tools_menu.addAction("Gemini 설정…", self.configure_gemini)
        status = self.statusBar()
        status.showMessage("준비")

    # ---- Tab 1: input & analysis -------------------------------------
    def _build_input_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*[SPACE["5"]] * 4)
        layout.setSpacing(SPACE["5"])

        input_box = QGroupBox("영상 입력")
        hbox = QHBoxLayout(input_box)
        hbox.setSpacing(SPACE["6"])
        for side in ("a", "b"):
            col = QVBoxLayout()
            col.setSpacing(SPACE["3"])
            header = QHBoxLayout()
            side_label = QLabel(f"[{side.upper()}]")
            side_label.setProperty("class", "strong")
            button = QPushButton(f"영상 {side.upper()} 선택")
            button.clicked.connect(lambda _=False, s=side: self._select_video(s))
            header.addWidget(side_label)
            header.addStretch(1)
            header.addWidget(button)
            col.addLayout(header)
            path_label = QLabel("파일을 선택하세요")
            path_label.setWordWrap(True)
            info_label = QLabel("")
            info_label.setProperty("class", "meta")
            info_label.setWordWrap(True)
            thumb = QLabel("미리보기 없음")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setMinimumSize(240, 150)
            thumb.setProperty("class", "plate")
            col.addWidget(path_label)
            col.addWidget(info_label)
            col.addWidget(thumb)
            setattr(self, f"_path_label_{side}", path_label)
            setattr(self, f"_info_label_{side}", info_label)
            setattr(self, f"_thumb_{side}", thumb)
            hbox.addLayout(col)
        layout.addWidget(input_box)

        settings_box = QGroupBox("분석 설정 & AI 보조")
        form = QFormLayout(settings_box)
        form.setSpacing(SPACE["3"])
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_MAP.keys()))
        self.quality_combo.setCurrentText("균형")
        form.addRow("정렬 품질", self.quality_combo)

        self.ai_check = QCheckBox("Gemini 보조 분석 사용")
        self.ai_model_edit = QLineEdit(self.ai_client.model)
        form.addRow("AI 사용", self.ai_check)
        form.addRow("Gemini 모델", self.ai_model_edit)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE["3"])
        self.btn_analyze = QPushButton("▶ 분석 시작")
        self.btn_analyze.setProperty("class", "primary")
        self.btn_analyze.clicked.connect(self.start_analysis)
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_worker)
        controls.addWidget(self.btn_analyze)
        controls.addWidget(self.btn_cancel)
        controls.addStretch(1)
        layout.addWidget(settings_box)
        layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.status_label = QLabel("아직 분석되지 않았습니다")
        self.status_label.setProperty("class", "meta")
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setReadOnly(True)
        self.summary_edit.setPlaceholderText("분석 및 정렬 요약이 여기에 표시됩니다.")
        layout.addWidget(self.summary_edit, 1)
        return widget

    # ---- Tab 2: alignment editor -------------------------------------
    def _build_edit_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*[SPACE["4"]] * 4)
        layout.setSpacing(SPACE["3"])
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        self.waveform = WaveformWidget()
        self.waveform.anchor_requested.connect(self._on_waveform_anchor)
        self.waveform.anchor_removed.connect(self._on_waveform_anchor_removed)
        self.waveform.time_selected.connect(self._on_waveform_time_selected)
        splitter.addWidget(self.waveform)

        bottom = QWidget()
        vbox = QVBoxLayout(bottom)
        vbox.setContentsMargins(0, SPACE["4"], 0, 0)
        vbox.setSpacing(SPACE["4"])

        anchor_row = QHBoxLayout()
        anchor_row.setSpacing(SPACE["3"])
        anchor_row.addWidget(QLabel("현재 위치(A) :"))
        self.current_a_spin = QDoubleSpinBox()
        self.current_a_spin.setRange(0, 60000)
        self.current_a_spin.setDecimals(2)
        self.current_a_spin.valueChanged.connect(self._on_current_a_changed)
        anchor_row.addWidget(self.current_a_spin)
        anchor_row.addWidget(QLabel("앵커 추가 A:"))
        self.anchor_a_spin = QDoubleSpinBox()
        self.anchor_a_spin.setRange(0, 60000)
        self.anchor_a_spin.setDecimals(2)
        anchor_row.addWidget(self.anchor_a_spin)
        anchor_row.addWidget(QLabel("B:"))
        self.anchor_b_spin = QDoubleSpinBox()
        self.anchor_b_spin.setRange(0, 60000)
        self.anchor_b_spin.setDecimals(2)
        anchor_row.addWidget(self.anchor_b_spin)
        self.btn_add_anchor = QPushButton("앵커 추가")
        self.btn_add_anchor.clicked.connect(self.add_anchor)
        anchor_row.addWidget(self.btn_add_anchor)
        anchor_row.addStretch(1)
        vbox.addLayout(anchor_row)

        trans_box = QGroupBox("전환 설정")
        trans_form = QFormLayout(trans_box)
        trans_form.setSpacing(SPACE["3"])
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.3, 600)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setValue(5.0)
        trans_form.addRow("전환 주기 (초)", self.interval_spin)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(MODE_MAP.keys()))
        trans_form.addRow("전환 모드", self.mode_combo)
        self.start_combo = QComboBox()
        self.start_combo.addItems(list(START_MAP.keys()))
        trans_form.addRow("첫 영상", self.start_combo)
        self.alternate_check = QCheckBox("A/B 교차 전환")
        self.alternate_check.setChecked(True)
        trans_form.addRow("교차", self.alternate_check)
        self.snap_check = QCheckBox("음악적 경계로 스냅")
        self.snap_check.setChecked(True)
        trans_form.addRow("스냅", self.snap_check)
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(list(EFFECT_MAP.keys()))
        self.effect_combo.setCurrentText("하드 컷")
        trans_form.addRow("전환 효과", self.effect_combo)
        self.effect_dur_spin = QDoubleSpinBox()
        self.effect_dur_spin.setRange(0.1, 3.0)
        self.effect_dur_spin.setDecimals(2)
        self.effect_dur_spin.setValue(0.35)
        trans_form.addRow("전환 길이 (초)", self.effect_dur_spin)
        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItems(list(AUDIO_MAP.keys()))
        trans_form.addRow("오디오 모드", self.audio_mode_combo)
        self.btn_regenerate = QPushButton("전환 생성")
        self.btn_regenerate.setProperty("class", "primary")
        self.btn_regenerate.clicked.connect(self.regenerate_transitions)
        trans_form.addRow("", self.btn_regenerate)
        vbox.addWidget(trans_box)

        tran_section = QLabel("전환 구간")
        tran_section.setProperty("class", "strong")
        vbox.addWidget(tran_section)

        self.tran_table = QTableWidget(0, 8)
        self.tran_table.setHorizontalHeaderLabels(["#", "표시", "A 시작", "A 끝", "B 시작", "신뢰도", "사유", "수정"])
        self.tran_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tran_table.verticalHeader().setVisible(False)
        self.tran_table.setAlternatingRowColors(True)
        self.tran_table.setShowGrid(False)
        vbox.addWidget(self.tran_table, 1)

        self.warnings_label = QLabel("")
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setProperty("class", "warn")
        vbox.addWidget(self.warnings_label)

        anchor_section = QLabel("앵커 — 수동 대응 지점 (파형에서 Ctrl-클릭 추가, 우클릭 삭제)")
        anchor_section.setProperty("class", "strong")
        vbox.addWidget(anchor_section)
        self.anchor_table = QTableWidget(0, 2)
        self.anchor_table.setHorizontalHeaderLabels(["A 시간(초)", "B 시간(초)"])
        self.anchor_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.anchor_table.verticalHeader().setVisible(False)
        self.anchor_table.setAlternatingRowColors(True)
        self.anchor_table.setShowGrid(False)
        vbox.addWidget(self.anchor_table)
        self.btn_remove_anchor = QPushButton("선택 앵커 삭제")
        self.btn_remove_anchor.clicked.connect(self.remove_selected_anchor)
        vbox.addWidget(self.btn_remove_anchor, 0, Qt.AlignmentFlag.AlignLeft)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        layout.addWidget(splitter)
        return widget

    # ---- Tab 3: preview -----------------------------------------------
    def _build_preview_tab(self):
        self.preview = _PreviewPane()
        self.preview.btn_prev.clicked.connect(
            lambda: self.preview.go_to_segment(self.preview.current_segment_index - 1)
        )
        self.preview.btn_next.clicked.connect(
            lambda: self.preview.go_to_segment(self.preview.current_segment_index + 1)
        )
        self.preview.btn_refresh.clicked.connect(lambda: self.preview.refresh_frames())
        self.preview.slider.valueChanged.connect(self._on_slider)
        return self.preview

    # ---- Tab 4: output ------------------------------------------------
    def _build_output_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(*[SPACE["5"]] * 4)
        layout.setSpacing(SPACE["5"])
        box = QGroupBox("출력 설정")
        form = QFormLayout(box)
        form.setSpacing(SPACE["3"])
        path_row = QHBoxLayout()
        path_row.setSpacing(SPACE["3"])
        self.output_path_edit = QLineEdit()
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(self._choose_output_path)
        path_row.addWidget(self.output_path_edit)
        path_row.addWidget(browse)
        form.addRow("출력 파일", path_row)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 7680)
        self.width_spin.setSpecialValueText("원본 해상도")
        form.addRow("출력 너비", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(0, 4320)
        self.height_spin.setSpecialValueText("원본 해상도")
        form.addRow("출력 높이", self.height_spin)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(5, 120)
        self.fps_spin.setValue(30.0)
        form.addRow("프레임 레이트", self.fps_spin)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(20)
        form.addRow("품질 (CRF, 낮을수록 고품질)", self.crf_spin)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(PRESET_MAP.keys()))
        self.preset_combo.setCurrentText("medium")
        form.addRow("인코딩 프리셋", self.preset_combo)
        self.include_audio_check = QCheckBox("오디오 포함")
        self.include_audio_check.setChecked(True)
        form.addRow("오디오", self.include_audio_check)
        layout.addWidget(box)

        controls = QHBoxLayout()
        controls.setSpacing(SPACE["3"])
        self.btn_render = QPushButton("▶ 최종 영상 생성")
        self.btn_render.setProperty("class", "primary")
        self.btn_render.clicked.connect(self.start_render)
        self.btn_render_cancel = QPushButton("취소")
        self.btn_render_cancel.setEnabled(False)
        self.btn_render_cancel.clicked.connect(self.cancel_worker)
        controls.addWidget(self.btn_render)
        controls.addWidget(self.btn_render_cancel)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.render_progress = QProgressBar()
        self.render_status = QLabel("")
        self.render_status.setProperty("class", "meta")
        layout.addWidget(self.render_progress)
        layout.addWidget(self.render_status)
        layout.addStretch(1)
        return widget

    # ------------------------------------------------------------------ UI events
    def _select_video(self, side: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"영상 {side.upper()} 선택", "", "영상 파일 (*.mp4 *.mov *.mkv *.avi *.m4v);;모든 파일 (*)"
        )
        if not path:
            return
        try:
            info = probe_media(path)
        except MediaError as exc:
            QMessageBox.critical(self, "파일 오류", str(exc))
            return
        setattr(self, f"_video_path_{side}", path)
        path_label = getattr(self, f"_path_label_{side}")
        path_label.setText(os.path.basename(path))
        path_label.setToolTip(path)
        info_text = " · ".join(
            f for f in [
                f"{info.summary()}",
                f"코덱 {info.video_codec}",
                f"오디오 {info.audio_codec} {info.sample_rate}Hz" if info.has_audio else "오디오 없음",
            ] if f
        )
        getattr(self, f"_info_label_{side}").setText(info_text)
        if info.has_video:
            png = extract_frame_png(path, 0.5, 320)
            thumb = getattr(self, f"_thumb_{side}")
            if png:
                pix = QPixmap()
                pix.loadFromData(png)
                thumb.setPixmap(pix.scaledToWidth(240, Qt.TransformationMode.SmoothTransformation))
                thumb.setText("")
        if getattr(self, "_video_path_a", "") and getattr(self, "_video_path_b", ""):
            self.status_label.setText("두 영상이 선택되었습니다. '분석 시작'을 누르세요.")

    def _tcfg(self) -> TransitionConfig:
        return TransitionConfig(
            interval_seconds=self.interval_spin.value(),
            mode=MODE_MAP[self.mode_combo.currentText()],
            start_with=START_MAP[self.start_combo.currentText()],
            alternate=self.alternate_check.isChecked(),
            snap_to_onset=self.snap_check.isChecked(),
            effect=EFFECT_MAP[self.effect_combo.currentText()],
            effect_duration=self.effect_dur_spin.value(),
            audio_mode=AUDIO_MAP[self.audio_mode_combo.currentText()],
        )

    def _acfg(self) -> AlignmentConfig:
        return AlignmentConfig(quality=QUALITY_MAP[self.quality_combo.currentText()])

    def _ocfg(self) -> OutputConfig:
        return OutputConfig(
            path=self.output_path_edit.text().strip(),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            fps=self.fps_spin.value(),
            crf=self.crf_spin.value(),
            preset=PRESET_MAP[self.preset_combo.currentText()],
            include_audio=self.include_audio_check.isChecked(),
        )

    def start_analysis(self):
        a_path = getattr(self, "_video_path_a", "")
        b_path = getattr(self, "_video_path_b", "")
        if not a_path or not b_path:
            QMessageBox.warning(self, "입력 필요", "분석할 두 영상을 먼저 선택하세요.")
            return
        if self.ai_check.isChecked():
            self.ai_client = GeminiClient(
                api_key=os.environ.get("GEMINI_API_KEY") or None,
                model=self.ai_model_edit.text().strip() or None,
            )
        else:
            self.ai_client = GeminiClient(model=self.ai_model_edit.text().strip() or None)
        self.project.engine = self.engine
        self.project.alignment_config = self._acfg()
        self.project.transition_config = self._tcfg()
        self._set_running(True, "분석 중…")
        self._current_worker = run_task(
            self,
            tasks.analyze_task,
            self._on_analysis_done,
            on_error=self._on_task_error,
            on_progress=self._on_analysis_progress,
            engine=self.engine,
            path_a=a_path,
            path_b=b_path,
            tcfg=self.project.transition_config,
            ai_client=self.ai_client if self.ai_check.isChecked() else None,
        )

    def _on_analysis_progress(self, fraction: float, message: str):
        self.progress.setValue(int(fraction * 100))
        self.status_label.setText(message)

    def _on_analysis_done(self, payload):
        self.perf_a = payload["perf_a"]
        self.perf_b = payload["perf_b"]
        self.project.perf_a = payload["perf_a"]
        self.project.perf_b = payload["perf_b"]
        self.project.alignment = payload["alignment"]
        self.project.plan = payload["plan"]
        self.env_a, self.env_b = payload["env_a"], payload["env_b"]
        self.project.transition_config = self._tcfg()
        self._set_running(False, "분석 완료")
        self.progress.setValue(100)

        text = "✔ 정렬 완료\n"
        a, b = self.perf_a, self.perf_b
        text += f"A: {os.path.basename(a.path)}  {a.media.summary()}  ({a.features.duration:.1f}s)\n"
        text += f"B: {os.path.basename(b.path)}  {b.media.summary()}  ({b.features.duration:.1f}s)\n"
        text += f"추정 템포 A {a.features.tempo_bpm:.0f} BPM / B {b.features.tempo_bpm:.0f} BPM\n"
        summary = self.project.alignment.summary()
        text += (
            f"평균 신뢰도 {summary['mean_confidence']:.2f}, "
            f"저신뢰도 구간 비율 {summary['low_confidence_fraction'] * 100:.1f}%\n"
        )
        low = self.project.alignment.low_confidence_segments_seconds()
        if low:
            text += "저신뢰도 구간: " + ", ".join(f"{s:.1f}~{e:.1f}s" for s, e in low) + "\n"
        if payload.get("ai_note"):
            text += "\n[AI] " + payload["ai_note"] + "\n"
        self.summary_edit.setPlainText(text)

        self._refresh_all_views()
        self.tabs.setCurrentIndex(1)

    def _refresh_all_views(self):
        self.current_a_spin.setMaximum(self.project.perf_a.features.duration if self.project.perf_a else 60000)
        self.waveform.set_data(self.env_a, self.env_b, self.project.alignment, self.project.plan, self.project.manual_points)
        self.waveform.set_current_time(self.current_a_spin.value())
        self._refresh_transition_table()
        self._refresh_anchor_table()
        self.preview.set_sources(
            self.project.perf_a.path if self.project.perf_a else "",
            self.project.perf_b.path if self.project.perf_b else "",
            self.project.alignment,
        )
        self.preview.set_segments(self.project.plan)
        if self.project.alignment:
            self.preview.show_time(0.0)
            self.preview.go_to_segment(0)

    def _refresh_transition_table(self):
        plan = self.project.plan
        rows = len(plan.segments) if plan else 0
        self.tran_table.setRowCount(rows)
        if not plan:
            return
        for r, seg in enumerate(plan.segments):
            values = [
                seg.index,
                seg.source,
                f"{seg.a_start:.2f}",
                f"{seg.a_end:.2f}",
                f"{seg.b_start:.2f}",
                f"{seg.confidence:.2f}",
                seg.reason,
                "✓" if seg.user_modified else "",
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 5 and seg.confidence < 0.35:
                    item.setForeground(QColor(TOKENS["DANGER"]))
                self.tran_table.setItem(r, c, item)
        self.warnings_label.setText("\n".join(plan.warnings) if plan.warnings else "")

    def _refresh_anchor_table(self):
        points = self.project.manual_points
        self.anchor_table.setRowCount(len(points))
        for r, (a, b) in enumerate(points):
            self.anchor_table.setItem(r, 0, QTableWidgetItem(f"{a:.3f}"))
            self.anchor_table.setItem(r, 1, QTableWidgetItem(f"{b:.3f}"))

    def regenerate_transitions(self):
        if not self.project.alignment:
            QMessageBox.warning(self, "필요", "먼저 분석을 완료하세요.")
            return
        try:
            plan = generate_transitions(
                self.project.alignment,
                self._tcfg(),
                user_points=self.project.manual_points,
            )
        except Exception as exc:
            QMessageBox.critical(self, "전환 생성 실패", friendly_error(exc))
            return
        self.project.plan = plan
        self.project.transition_config = self._tcfg()
        self._refresh_all_views()
        self.statusBar().showMessage(f"전환 지점 {len(plan.segments)}개 생성됨")

    def add_anchor(self):
        if not self.project.alignment:
            return
        a_t = self.anchor_a_spin.value()
        b_t = self.anchor_b_spin.value()
        self.project.manual_points = [
            p for p in self.project.manual_points if not (abs(p[0] - a_t) < 1e-3 and abs(p[1] - b_t) < 1e-3)
        ]
        self.project.manual_points.append((a_t, b_t))
        self.regenerate_transitions()

    def remove_selected_anchor(self):
        rows = sorted({idx.row() for idx in self.anchor_table.selectedIndexes()})
        if not rows:
            return
        for r in reversed(rows):
            if 0 <= r < len(self.project.manual_points):
                self.project.manual_points.pop(r)
        self.regenerate_transitions()

    def _on_current_a_changed(self, value: float):
        self.waveform.set_current_time(value)
        if self.project.alignment and not self.preview.slider.isSliderDown():
            self.preview.show_time(value)

    def _on_slider(self, value: int):
        if self.project.alignment:
            duration = self.project.alignment.feat_a.times[-1]
            self.waveform.set_current_time(duration * value / 1000.0)
            self.preview.show_time(duration * value / 1000.0)

    def _on_waveform_time_selected(self, a_time: float):
        self.current_a_spin.setValue(float(a_time))

    def _on_waveform_anchor(self, a_t: float, b_t: float):
        self.anchor_a_spin.setValue(float(a_t))
        self.anchor_b_spin.setValue(float(b_t))
        self.add_anchor()

    def _on_waveform_anchor_removed(self, a_t: float, b_t: float):
        self.project.manual_points = [
            p for p in self.project.manual_points
            if not (abs(p[0] - a_t) < 1e-3 and abs(p[1] - b_t) < 1e-3)
        ]
        self.regenerate_transitions()

    # ---- rendering -----------------------------------------------------
    def _choose_output_path(self):
        path, _ = QFileDialog.getSaveFileName(self, "출력 영상 저장", "comparison.mp4", "영상 (*.mp4)")
        if path:
            self.output_path_edit.setText(path)

    def start_render(self):
        if not self.project.plan or not self.project.alignment:
            QMessageBox.warning(self, "필요", "먼저 분석과 전환 지점 생성이 필요합니다.")
            return
        out_path = self.output_path_edit.text().strip() or os.path.expanduser("~/piano_sync_comparison.mp4")
        self.output_path_edit.setText(out_path)
        self.project.output_config = self._ocfg()
        self.project.transition_config = self._tcfg()
        self._current_worker = run_task(
            self,
            tasks.render_task,
            self._on_render_done,
            on_error=self._on_render_error,
            on_progress=lambda f, m: (self.render_progress.setValue(int(f * 100)), self.render_status.setText(m)),
            plan=self.project.plan,
            alignment=self.project.alignment,
            media_a=self.project.perf_a.media,
            media_b=self.project.perf_b.media,
            wav_a=self.project.perf_a.wav_path,
            wav_b=self.project.perf_b.wav_path,
            ocfg=self.project.output_config,
            tcfg=self.project.transition_config,
        )
        self.btn_render.setEnabled(False)
        self.btn_render_cancel.setEnabled(True)
        self.statusBar().showMessage("영상 생성 중…")

    def _on_render_done(self, result):
        self.btn_render.setEnabled(True)
        self.btn_render_cancel.setEnabled(False)
        self.render_progress.setValue(100)
        self.render_status.setText(
            f"완료: {result.path}  ({result.duration:.1f}s, {result.width}x{result.height}, "
            f"{result.segments}개 구간)"
        )
        self.statusBar().showMessage("영상 생성 완료")
        QMessageBox.information(self, "완료", f"최종 영상이 생성되었습니다.\n{result.path}")

    def _on_render_error(self, text: str):
        self.btn_render.setEnabled(True)
        self.btn_render_cancel.setEnabled(False)
        self.render_status.setText(f"오류: {text}")
        QMessageBox.critical(self, "렌더링 실패", text)

    # ---- generic worker control ----------------------------------------
    def _set_running(self, running: bool, message: str):
        self.btn_analyze.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        self.btn_regenerate.setEnabled(not running)
        if running:
            self.status_label.setText(message)
        else:
            self.statusBar().showMessage(message)

    def cancel_worker(self):
        if self._current_worker:
            self._current_worker.cancel()

    def _on_task_error(self, text: str):
        self._set_running(False, "실패")
        self.statusBar().showMessage("실패")
        QMessageBox.critical(self, "작업 실패", text)

    # ---- project io ----------------------------------------------------
    def new_project(self):
        self.project = Project(engine=self.engine)
        self.env_a = self.env_b = None
        self.summary_edit.clear()
        self._clear_input_labels()
        self._refresh_all_views()
        self.statusBar().showMessage("새 프로젝트")

    def _clear_input_labels(self):
        for side in ("a", "b"):
            getattr(self, f"_path_label_{side}").setText("파일을 선택하세요")
            getattr(self, f"_info_label_{side}").setText("")
            thumb = getattr(self, f"_thumb_{side}")
            thumb.clear()
            thumb.setText("미리보기 없음")

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", "piano_sync_project.json", "JSON (*.json)")
        if not path:
            return
        self._persist_output_config()
        try:
            save_project(self.project, path)
        except ProjectError as exc:
            QMessageBox.critical(self, "저장 실패", str(exc))
            return
        self.statusBar().showMessage(f"저장됨: {path}")

    def save_project_current(self):
        if not self.project.file_path:
            self.save_project_as()
            return
        self._persist_output_config()
        try:
            save_project(self.project, self.project.file_path)
        except ProjectError as exc:
            QMessageBox.critical(self, "저장 실패", str(exc))
        self.statusBar().showMessage("저장됨")

    def _persist_output_config(self):
        self.project.output_config = self._ocfg()
        self.project.transition_config = self._tcfg()
        self.project.alignment_config = self._acfg()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", "JSON (*.json);;모든 파일 (*)")
        if not path:
            return
        try:
            project = load_project(path, self.engine)
        except ProjectError as exc:
            QMessageBox.warning(self, "열기 실패", str(exc))
            return
        self.project = project
        self._load_video_info_from_project()
        self._refresh_all_views()
        self.statusBar().showMessage(f"열림: {path}")
        self.tabs.setCurrentIndex(1)

    def _load_video_info_from_project(self):
        for side, perf in (("a", self.project.perf_a), ("b", self.project.perf_b)):
            if not perf:
                continue
            setattr(self, f"_video_path_{side}", perf.path)
            getattr(self, f"_path_label_{side}").setText(os.path.basename(perf.path))
            getattr(self, f"_path_label_{side}").setToolTip(perf.path)
            info = " · ".join(
                f for f in [
                    f"{perf.media.summary()}",
                    f"오디오 {perf.media.audio_codec} {perf.media.sample_rate}Hz" if perf.media.has_audio else "오디오 없음",
                ] if f
            )
            getattr(self, f"_info_label_{side}").setText(info)

    def configure_gemini(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Gemini 설정")
        form = QFormLayout(dialog)
        key = QLineEdit(os.environ.get("GEMINI_API_KEY", ""))
        key.setEchoMode(QLineEdit.EchoMode.Password)
        model = QLineEdit(self.ai_client.model)
        note = QLabel("키는 프로젝트 파일에 저장되지 않습니다. 키 없이도 로컬 분석은 동작합니다.")
        note.setWordWrap(True)
        form.addRow("API 키", key)
        form.addRow("모델", model)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            os.environ["GEMINI_API_KEY"] = key.text().strip()
            self.ai_client = GeminiClient(
                api_key=key.text().strip() or None, model=model.text().strip() or None
            )
            self.ai_model_edit.setText(self.ai_client.model)
            self.ai_check.setChecked(bool(key.text().strip()))
            self.statusBar().showMessage("Gemini 설정 저장됨")

    def _sync_from_project(self):
        """Push persisted settings into the widgets when loading a project."""
        tcfg = self.project.transition_config
        self.interval_spin.setValue(tcfg.interval_seconds)
        self.mode_combo.setCurrentText(next((k for k, v in MODE_MAP.items() if v == tcfg.mode), "음악 위치 기준 (모드 1)"))
        self.start_combo.setCurrentText(next((k for k, v in START_MAP.items() if v == tcfg.start_with), "영상 A"))
        self.alternate_check.setChecked(tcfg.alternate)
        self.snap_check.setChecked(tcfg.snap_to_onset)
        self.effect_combo.setCurrentText(next((k for k, v in EFFECT_MAP.items() if v == tcfg.effect), "하드 컷"))
        self.effect_dur_spin.setValue(tcfg.effect_duration)
        self.audio_mode_combo.setCurrentText(next((k for k, v in AUDIO_MAP.items() if v == tcfg.audio_mode), "영상 A"))
        ocfg = self.project.output_config
        self.output_path_edit.setText(ocfg.path)
        self.width_spin.setValue(ocfg.width)
        self.height_spin.setValue(ocfg.height)
        self.fps_spin.setValue(ocfg.fps)
        self.crf_spin.setValue(ocfg.crf)
        self.preset_combo.setCurrentText(ocfg.preset if ocfg.preset in PRESET_MAP else "medium")
        self.include_audio_check.setChecked(ocfg.include_audio)