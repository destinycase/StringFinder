"""검색 결과의 복사·탐색기 열기·내보내기(TXT/XLSX).

8.1.4에서 result_view.py에서 옮겼습니다. ResultView가 이 믹스인을 상속해 메서드 이름·동작은 그대로입니다.
메서드는 ResultView의 표·모델(`result_view`, `proxy_model`, `result_model`, `match_view`, `match_model`,
`match_proxy_model`, `search_mode`)을 씁니다. 오른쪽 메뉴를 만드는 코드는 ResultView에 남아 있습니다.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger


_EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _sanitize_excel_cell(value):
    """Return user-controlled text as a literal Excel cell value."""
    if isinstance(value, str):
        # Excel이 받지 않는 제어 문자(로그의 색 코드 ESC 등)가 하나라도 있으면 파일 전체 저장이
        # 실패하므로 지웁니다(검수 U-5).
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        if value.startswith(_EXCEL_FORMULA_PREFIXES):
            return "'" + value
    return value


def _text_or_empty(value):
    """내보낼 값: 없으면 빈 칸(None이라고 쓰지 않음, 검수 U-10)."""
    return "" if value is None else str(value)


def _append_excel_row(worksheet, values):
    worksheet.append([_sanitize_excel_cell(value) for value in values])


def _result_note(result_model, path) -> str:
    """내보낼 때 쓸 파일 목록 기타 칸의 사유 문장입니다(지금 언어)."""
    note_for = getattr(result_model, "note_for", None)
    if not callable(note_for):
        return ""
    from core.skip_reason_formatter import render_skip_reason

    return render_skip_reason(note_for(path))


if TYPE_CHECKING:
    _MixinBase = QWidget  # 타입 검사에서는 ResultView(QWidget)의 일부로 봅니다.
else:
    _MixinBase = object


class ResultCopyExportMixin(_MixinBase):
    """ResultView의 복사·탐색기 열기·내보내기 메서드."""

    # ResultView가 만드는 표·모델입니다(타입 검사용 선언, 실행 때는 값이 없습니다).
    result_view: Any
    proxy_model: Any
    result_model: Any
    match_view: Any
    match_model: Any
    match_proxy_model: Any
    search_mode: Any

    def _selected_result_paths_in_order(self) -> list:
        """고른 파일들의 전체 경로를 파일 목록에 보이는 순서대로 돌려줍니다. 고른 것이 없으면 현재 위치 한 줄입니다."""
        selection = self.result_view.selectionModel().selectedRows()
        if not selection:
            index = self.result_view.currentIndex()
            selection = [index] if index.isValid() else []
        paths = []
        for row in sorted({index.row() for index in selection}):
            source_index = self.proxy_model.mapToSource(self.proxy_model.index(row, 0))
            file_path, _ = self.result_model.get_full_data(source_index.row())
            if file_path:
                paths.append(file_path)
        return paths

    def _copy_selected_result_file_names(self):
        """고른 파일들의 이름(폴더 제외)을 한 줄에 하나씩 복사합니다(7.7.4)."""
        names = [os.path.basename(path.replace("\\", "/")) for path in self._selected_result_paths_in_order()]
        if names:
            self._copy_to_clipboard("\n".join(names))

    def _copy_selected_result_folders(self):
        """고른 파일들이 있는 폴더를 처음 나온 순서대로, 같은 폴더는 한 번만 복사합니다(7.7.4).

        Windows처럼 대소문자와 / \\ 차이는 같은 폴더로 봅니다. 복사하는 글자는 처음 나온 표기 그대로입니다.
        """
        folders = []
        seen = set()
        for path in self._selected_result_paths_in_order():
            folder = os.path.dirname(path)
            key = os.path.normcase(os.path.normpath(folder)) if folder else ""
            if key in seen:
                continue
            seen.add(key)
            folders.append(folder)
        if folders:
            self._copy_to_clipboard("\n".join(folders))

    def _copy_selected_result_path(self):
        # 파일만·폴더만 복사와 같은 목록 순서로 복사해, 따로 복사해 나란히 붙여도 줄이 맞습니다(7.7.5).
        paths = self._selected_result_paths_in_order()
        if paths:
            self._copy_to_clipboard("\n".join(paths))

    def _copy_selected_match_content(self):
        selection = self.match_view.selectionModel().selectedRows()
        if not selection:
            index = self.match_view.currentIndex()
            if index.isValid():
                selection = [index]
            else:
                return
        contents = []
        # 화면에 보이는 데이터(필터 적용됨)를 목록 순서로 복사합니다. 칸별 복사와 순서가 같습니다(7.7.5).
        for row in sorted({index.row() for index in selection}):
            row_data = []
            for col in range(self.match_proxy_model.columnCount()):
                val = self.match_proxy_model.data(
                    self.match_proxy_model.index(row, col), Qt.ItemDataRole.EditRole
                )
                row_data.append(str(val))
            contents.append("\t".join(row_data))
        if contents:
            self._copy_to_clipboard("\n".join(contents))

    def _match_copy_field_labels(self) -> list:
        """검색 상세 메뉴의 "칸 하나만 복사" 항목 이름입니다. 순서는 MatchDetailModel.copy_fields와 같습니다."""
        model = self.match_model
        if model.columnCount() <= 1:
            return [AppStrings.COPY_LOCATION_ONLY, AppStrings.COPY_TEXT_ONLY]
        return [
            AppStrings.COPY_COLUMN_ONLY.format(model.headerData(col, Qt.Orientation.Horizontal))
            for col in range(model.columnCount())
        ]

    def _selected_match_source_rows(self) -> list:
        """고른 상세 행의 원본 모델 행 번호를 화면 순서대로 돌려줍니다(파일 머리줄 포함, 복사 때 건너뜀)."""
        selection = self.match_view.selectionModel().selectedRows()
        if not selection:
            index = self.match_view.currentIndex()
            selection = [index] if index.isValid() else []
        proxy_rows = sorted({index.row() for index in selection})
        return [self.match_proxy_model.mapToSource(self.match_proxy_model.index(row, 0)).row() for row in proxy_rows]

    def _copy_selected_match_field(self, field_index: int):
        """고른 상세 행들에서 칸 하나만 줄마다 복사합니다(7.6.3). 예: 위치만, 키만, 값만."""
        lines = []
        for source_row in self._selected_match_source_rows():
            fields = self.match_model.copy_fields(source_row)
            if fields is None or field_index >= len(fields):
                continue
            lines.append(fields[field_index])
        if lines:
            self._copy_to_clipboard("\n".join(lines))

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _open_file_location(self, file_path):
        """파일이 있는 폴더를 탐색기에서 열고 그 파일을 선택합니다.

        탐색기가 끝날 때까지 기다리지 않으며, 파일이 없거나 실행하지 못하면 안내합니다(7.1.29).
        """
        if not file_path or not os.path.exists(file_path):
            self._show_open_location_error(file_path)
            return
        try:
            if os.name == "nt":
                command = ["explorer", "/select,", os.path.normpath(file_path)]
            else:
                command = ["open" if sys.platform == "darwin" else "xdg-open", os.path.dirname(file_path)]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(AppStrings.LOG_OPEN_FOLDER_FAIL.format(file_path, error))
            self._show_open_location_error(file_path)

    def _show_open_location_error(self, file_path):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, AppStrings.ERROR_TITLE, AppStrings.ERROR_OPEN_FOLDER.format(file_path or ""))

    def _export_results(self):
        if self.result_model.rowCount() == 0:
            return
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, AppStrings.RESULT_EXPORT_TITLE, "", AppStrings.RESULT_EXPORT_FILTER
        )
        if not file_path:
            return
        export_path = Path(file_path)
        if not export_path.suffix:
            export_path = export_path.with_suffix(".xlsx" if "*.xlsx" in selected_filter else ".txt")
        file_path = str(export_path)
        try:
            if Path(file_path).suffix.lower() == ".xlsx":
                self._export_to_excel(file_path)
            else:
                self._export_to_text(file_path)
        except Exception as e:
            logger.error(AppStrings.ERROR_EXPORT_FAIL.format(str(e)))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, AppStrings.RESULT_EXPORT_TITLE, AppStrings.ERROR_EXPORT_FAIL.format(str(e)))
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, AppStrings.RESULT_EXPORT_TITLE, AppStrings.EXPORT_SUCCESS.format(file_path))

    def _export_to_excel(self, file_path):
        import openpyxl

        temp_path = None
        target = Path(file_path)
        wb = openpyxl.Workbook()

        # [Sheet 1] 검색 파일 목록
        ws1 = wb.active
        ws1.title = AppStrings.EXCEL_SHEET_FILE_LIST
        _append_excel_row(ws1, [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_MATCH_COUNT,
                                AppStrings.HEADER_NOTE])

        # [Sheet 2] 검색 상세
        ws2 = wb.create_sheet(title=AppStrings.EXCEL_SHEET_MATCH_DETAILS)

        # 모드별 헤더 설정
        mode = self.search_mode or Constants.MODE_NORMAL
        mode_upper = str(mode).upper()
        if Constants.MODE_ARCHIVE.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_LINE_NUMBER,
                AppStrings.HEADER_ARCHIVE_NAMESPACE,
                AppStrings.HEADER_ARCHIVE_KEY,
                AppStrings.HEADER_ARCHIVE_SOURCE,
                AppStrings.HEADER_ARCHIVE_TRANSLATION,
            ]
        elif Constants.MODE_EXCEL.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_EXCEL_SHEET,
                AppStrings.HEADER_EXCEL_CELL,
                AppStrings.HEADER_EXCEL_VALUE,
            ]
        elif Constants.MODE_JSON.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_POSITION,
                AppStrings.HEADER_JSON_KEY,
                AppStrings.HEADER_JSON_VALUE,
            ]
        elif Constants.MODE_XML.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_POSITION,
                AppStrings.HEADER_XML_NAME,
                AppStrings.HEADER_XML_VALUE,
            ]
        else:
            headers = [AppStrings.HEADER_FILE, AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

        _append_excel_row(ws2, headers)

        all_results = self.result_model.get_all_results()

        for i, (count, path, folder, full_path, matches) in enumerate(all_results):
            # 시트 1 데이터 추가
            _append_excel_row(ws1, [i + 1, full_path, count, _result_note(self.result_model, full_path).replace("\n", " / ")])

            # 시트 2 데이터 추가
            for m in matches:
                # 파일당 상한·부분 검색 안내 행(-1/-2)은 일치가 아니므로 내보내지 않습니다(검수 U-10).
                if str(m[0]) in {"-1", "-2"}:
                    continue
                row = [full_path]
                if Constants.MODE_ARCHIVE.upper() in mode_upper:
                    # [Key 줄 번호, Namespace, Key, 원문, 번역문, ...]
                    row.extend([self._export_value(value) for value in list(m[:5]) + [""] * (5 - len(m[:5]))])
                elif Constants.MODE_EXCEL.upper() in mode_upper:
                    # [Line, Sheet, Cell, Val, ...]
                    row.extend([_text_or_empty(m[1]), _text_or_empty(m[2]), _text_or_empty(m[3])])
                elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
                    # [Line, Name/Path, Value, ...]
                    row.extend([_text_or_empty(m[0]), _text_or_empty(m[1]), _text_or_empty(m[2])])
                else:
                    # 기본 모드: [Line, Content]. 일반 검색에 섞인 Excel 결과(시트<탭>셀<탭>값)는 화면처럼 보이게 바꿉니다.
                    content = _text_or_empty(m[1])
                    if "\t" in content:
                        sheet, cell, value = (content.split("\t", 2) + ["", ""])[:3]
                        content = f"{sheet}!{cell} | {value}"
                    row.extend([_text_or_empty(m[0]), content])
                _append_excel_row(ws2, row)

        temp_file = tempfile.NamedTemporaryFile(
            prefix=f".{target.stem}_", suffix=target.suffix, dir=target.parent,
            delete=False,
        )
        temp_path = temp_file.name
        temp_file.close()
        try:
            wb.save(temp_path)
            os.replace(temp_path, file_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def _export_to_text(self, file_path):
        target = Path(file_path)
        temp_path = None
        try:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", prefix=f".{target.stem}_",
                suffix=target.suffix, dir=target.parent, delete=False,
            )
            temp_path = temp_file.name
            temp_file.close()
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
                f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{AppStrings.DOCK_RESULT_TITLE}\n\n")
                all_results = self.result_model.get_all_results()
                for count, path, folder, full_path, matches in all_results:
                    f.write(f"[{count}] {full_path}\n")
                    for note_line in _result_note(self.result_model, full_path).splitlines():
                        f.write(f"  ⚠ {note_line}\n")
                    for item in matches:
                        fields = self._match_fields_for_export(item)
                        if fields:
                            line_no, content = fields[0], "\t".join(fields[1:])
                            f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content) + "\n")
                    f.write(AppStrings.EXPORT_TEXT_SEPARATOR + "\n")
            os.replace(temp_path, file_path)
            temp_path = None
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @staticmethod
    def _export_value(value):
        return "" if value is None else str(value)

    def _match_fields_for_export(self, item):
        """Return the meaningful fields for the active search mode."""
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return ()
        values = [self._export_value(value) for value in item]
        mode = str(self.search_mode or "").upper()
        if Constants.MODE_ARCHIVE.upper() in mode:
            return tuple(values[:5])
        if Constants.MODE_EXCEL.upper() in mode:
            return tuple(values[:4])
        if Constants.MODE_JSON.upper() in mode or Constants.MODE_XML.upper() in mode:
            return tuple(values[:3])
        return tuple(values[:2])
