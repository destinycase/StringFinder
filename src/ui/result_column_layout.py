"""검색 상세 표의 열 폭: 보이는 폭 감시와 폭 나누기 계산.

8.1.3에서 result_view.py에서 옮겼습니다. 동작은 그대로입니다.
"""

from PySide6.QtCore import QEvent, QObject


class MatchColumnWatcher(QObject):
    """검색 상세 표의 보이는 폭 변화와 열 경계 끌기 끝을 알려 줍니다(7.5.4).

    미리보기 창과의 분할선을 옮기거나 창 크기를 바꾸면 표의 보이는 폭이 바뀌어 열 폭을 다시 나눕니다.
    """

    def __init__(self, view, on_width_changed, on_drag_finished):
        super().__init__(view)
        self._view = view
        self._on_width_changed = on_width_changed
        self._on_drag_finished = on_drag_finished
        self._last_width = -1
        # 머리글에서 마우스를 누르고 있는 동안만 참입니다. 이때 바뀐 열 폭만 "사용자가 끈 폭"으로 봅니다(7.5.6).
        self.header_pressed = False
        # 표가 지워지는 중에도 이벤트가 오므로, 감시 대상은 미리 잡아 두고 이벤트마다 표를 부르지 않습니다.
        self._viewport = view.viewport()
        self._header_viewport = view.horizontalHeader().viewport()
        self._viewport.installEventFilter(self)
        self._header_viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type == QEvent.Type.Resize and watched is self._viewport:
            width = watched.width()
            if width != self._last_width:
                self._last_width = width
                self._on_width_changed()
        elif watched is self._header_viewport:
            if event_type == QEvent.Type.MouseButtonPress:
                self.header_pressed = True
            elif event_type == QEvent.Type.MouseButtonRelease:
                self.header_pressed = False
                self._on_drag_finished()
        return False


def compute_match_column_widths(available, count, natural, value_columns, ratios, *, minimum, key_min,
                                key_max_ratio, position_max, value_min_ratio) -> list:
    """보이는 폭 `available`을 열마다 나눕니다. 합이 정확히 `available`이 되어 가로 스크롤이 생기지 않습니다.

    - 사용자가 직접 끈 비율(`ratios`)이 있으면 그 비율대로 나눕니다.
    - 없으면 위치 열은 내용 폭(최대 `position_max`), 이름·키 열은 내용 폭이되 표 폭의 `key_max_ratio`(최소 `key_min`)까지,
      남는 폭은 값 열(`value_columns`, UE archive는 원문·번역)이 똑같이 나눕니다. 값 열 몫이 표 폭의 `value_min_ratio`보다
      작으면 이름·키 열을 같은 비율로 줄입니다. `natural`(열마다 한 줄 폭)이 없으면 기본값을 씁니다.
    """
    if count <= 0:
        return []
    available = max(int(available), minimum * count)
    if ratios and len(ratios) == count:
        widths = [max(minimum, int(available * ratio)) for ratio in ratios]
    else:
        natural = list(natural) if natural and len(natural) == count else [60] + [150] * (count - 1)
        key_cap = max(key_min, int(available * key_max_ratio))
        widths = [0] * count
        fixed_columns = [column for column in range(count) if column not in value_columns]
        for column in fixed_columns:
            cap = position_max if column == 0 else key_cap
            widths[column] = max(minimum, min(natural[column], cap))
        value_floor = int(available * value_min_ratio)
        fixed_total = sum(widths[column] for column in fixed_columns)
        if fixed_total > available - value_floor and fixed_total > 0:
            scale = max(0.0, (available - value_floor) / fixed_total)
            for column in fixed_columns:
                widths[column] = max(minimum, int(widths[column] * scale))
        remaining = available - sum(widths[column] for column in fixed_columns)
        share = max(minimum, remaining // len(value_columns))
        for column in value_columns:
            widths[column] = share
    # 반올림으로 남거나 모자란 폭은 마지막 열이 맡아 합을 표 폭에 정확히 맞춥니다.
    widths[-1] = max(minimum, widths[-1] + available - sum(widths))
    return widths
