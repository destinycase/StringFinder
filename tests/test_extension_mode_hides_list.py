"""검색 모드를 쓰는 동안 확장자 목록을 숨기고 안내만 보이는지 확인합니다(7.1.10)."""
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.panels import ExtensionFilterPanel

XML_MODE = AppStrings.SPECIAL_SEARCH_ITEMS[1]


def test_mode_hides_list_and_controls_but_keeps_items(qtbot):
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.restore_state({C.CONFIG_KEY_EXTENSIONS: {"txt": True, "log": False}})
    before = panel.get_state()[C.CONFIG_KEY_EXTENSIONS]
    panel.set_special_mode(XML_MODE)
    assert panel.values_only_notice.isVisible()
    for widget in (panel.ext_list, panel.ext_edit, panel.add_btn, panel.sel_all_btn, panel.desel_all_btn):
        assert not widget.isVisible()
    assert panel.get_state()[C.CONFIG_KEY_EXTENSIONS] == before
    panel.set_special_mode(AppStrings.SPECIAL_SEARCH_OFF)
    assert not panel.values_only_notice.isVisible()
    assert panel.ext_list.isVisible() and panel.ext_edit.isVisible()
    assert panel.get_state()[C.CONFIG_KEY_EXTENSIONS] == before
    assert panel.get_checked_extensions() == ["txt"]
