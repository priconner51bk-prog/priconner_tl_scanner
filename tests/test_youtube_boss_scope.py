import pytest

import youtube_search


def test_selected_bosses_can_isolate_boss_three():
    assert youtube_search.selected_bosses(["A", "B", "C", "D"], "3") == [(3, "C")]


def test_selected_bosses_rejects_invalid_scope():
    with pytest.raises(ValueError):
        youtube_search.selected_bosses(["A", "B", "C"], "4")
