"""
프로파일링 UI 위젯

실시간 성능 대시보드를 표시하는 QDockWidget
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar, QPushButton, QCheckBox, QGroupBox, QHBoxLayout
from PySide6.QtCore import QTimer
from profiling.metrics_collector import MetricsCollector
from profiling.analyzer import PerformanceAnalyzer
from utils.app_strings import AppStrings


class ProfilingWidget(QWidget):
    """
    성능 프로파일링 실시간 대시보드

    검색 중 CPU, 메모리, 검색 속도를 실시간으로 표시하고,
    검색 완료 후 히스토리를 저장합니다.
    """

    def __init__(self, config_manager):
        super().__init__()
        self.config_manager = config_manager
        self.collector = None
        self.analyzer = PerformanceAnalyzer(config_manager)
        self.enabled = config_manager.get("enable_profiler", False)

        self._init_ui()

        # 100ms마다 업데이트
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_display)
        self.update_timer.setInterval(100)

    def _init_ui(self):
        """UI 초기화"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 활성화 체크박스
        self.enable_checkbox = QCheckBox("프로파일링 활성화")
        self.enable_checkbox.setChecked(self.enabled)
        self.enable_checkbox.stateChanged.connect(self._on_enable_changed)
        layout.addWidget(self.enable_checkbox)

        # 실시간 메트릭 그룹
        metrics_group = QGroupBox("실시간 메트릭")
        metrics_layout = QVBoxLayout()

        # CPU 사용률
        cpu_layout = QHBoxLayout()
        self.cpu_label = QLabel("CPU: 0%")
        self.cpu_label.setMinimumWidth(100)
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setMaximum(100)
        cpu_layout.addWidget(self.cpu_label)
        cpu_layout.addWidget(self.cpu_bar)
        metrics_layout.addLayout(cpu_layout)

        # 메모리 사용량
        memory_layout = QHBoxLayout()
        self.memory_label = QLabel("메모리: 0 MB")
        self.memory_label.setMinimumWidth(100)
        self.memory_bar = QProgressBar()
        self.memory_bar.setMaximum(1000)  # 1GB 기준
        memory_layout.addWidget(self.memory_label)
        memory_layout.addWidget(self.memory_bar)
        metrics_layout.addLayout(memory_layout)

        # 검색 속도
        self.speed_label = QLabel("속도: 0 files/sec")
        metrics_layout.addWidget(self.speed_label)

        # 처리 파일 수
        self.files_label = QLabel("처리: 0 files")
        metrics_layout.addWidget(self.files_label)

        # 매칭 수
        self.matches_label = QLabel("매칭: 0 matches")
        metrics_layout.addWidget(self.matches_label)

        metrics_group.setLayout(metrics_layout)
        layout.addWidget(metrics_group)

        # 히스토리 버튼
        self.history_btn = QPushButton("히스토리 보기")
        self.history_btn.clicked.connect(self._show_history)
        layout.addWidget(self.history_btn)

        layout.addStretch()

    def _on_enable_changed(self, state):
        """프로파일링 활성화 상태 변경"""
        self.enabled = bool(state)
        self.config_manager.config["enable_profiler"] = self.enabled
        self.config_manager.save()

    def is_enabled(self) -> bool:
        """프로파일링 활성화 여부 반환"""
        return self.enabled

    def start_profiling(self):
        """프로파일링 시작"""
        if not self.enabled:
            return

        self.collector = MetricsCollector()
        self.collector.start()
        self.update_timer.start()

        # UI 초기화
        self._reset_display()

    def stop_profiling(self):
        """프로파일링 종료 및 리포트 저장"""
        self.update_timer.stop()

        if self.collector:
            summary = self.collector.get_summary()
            if summary:
                self.analyzer.save_report(summary)
            self.collector = None

    def update_progress(self, files_processed: int, matches_found: int):
        """
        검색 진행 상황 업데이트

        Args:
            files_processed: 처리된 파일 수
            matches_found: 발견된 매칭 수
        """
        if self.collector:
            self.collector.collect(files_processed, matches_found)

    def _update_display(self):
        """실시간 디스플레이 업데이트"""
        if not self.collector or not self.collector.metrics:
            return

        latest = self.collector.metrics[-1]

        # CPU 업데이트
        self.cpu_label.setText(f"CPU: {latest.cpu_percent:.1f}%")
        self.cpu_bar.setValue(int(latest.cpu_percent))

        # 메모리 업데이트
        self.memory_label.setText(f"메모리: {latest.memory_mb:.1f} MB")
        self.memory_bar.setValue(int(latest.memory_mb))

        # 검색 속도 업데이트
        if latest.timestamp > 0:
            speed = latest.files_processed / latest.timestamp
            self.speed_label.setText(f"속도: {speed:.0f} files/sec")

        # 처리 파일 수 업데이트
        self.files_label.setText(f"처리: {latest.files_processed:,} files")

        # 매칭 수 업데이트
        self.matches_label.setText(f"매칭: {latest.matches_found:,} matches")

    def _reset_display(self):
        """디스플레이 초기화"""
        self.cpu_label.setText("CPU: 0%")
        self.cpu_bar.setValue(0)
        self.memory_label.setText(AppStrings.PROFILING_MEMORY_LABEL.format(0))
        self.memory_bar.setValue(0)
        self.speed_label.setText(AppStrings.PROFILING_SPEED_LABEL.format(0))
        self.files_label.setText(AppStrings.PROFILING_FILES_LABEL.format(0))
        self.matches_label.setText(AppStrings.PROFILING_MATCHES_LABEL.format(0))

    def _show_history(self):
        """히스토리 다이얼로그 표시"""
        from PySide6.QtWidgets import QMessageBox

        trend = self.analyzer.get_trend(days=30)
        if not trend:
            QMessageBox.information(self, AppStrings.PROFILING_HISTORY_TITLE_SHORT, AppStrings.PROFILING_NO_HISTORY)
            return

        msg = f"""최근 30일 성능 트렌드:

총 검색 횟수: {trend["total_searches"]}회
평균 속도: {trend["avg_speed"]:.0f} files/sec
평균 메모리: {trend["avg_memory"]:.1f} MB
평균 검색 시간: {trend["avg_duration"]:.2f}초
"""

        QMessageBox.information(self, AppStrings.PROFILING_HISTORY_TITLE, msg)
