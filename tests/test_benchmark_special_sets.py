"""
Benchmark special dataset coverage for Excel mode.
"""

import sys
from pathlib import Path

import pytest

from sf_utils.constants import Constants


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.benchmark_performance as benchmark_performance  # noqa: E402


@pytest.mark.stress
def test_benchmark_special_dataset_generation_and_hits(tmp_path):
    set_special_dir = tmp_path / "set_special"
    excel_path = set_special_dir / "excel_mix.xlsx"

    benchmark_performance._create_set_j_excel_file(excel_path)

    assert excel_path.exists()
    assert excel_path.stat().st_size > 0

    excel_result = benchmark_performance.run_benchmark(
        "Set J: Excel Mixed",
        excel_path,
        benchmark_performance.SET_J_KEYWORD,
        special_mode=Constants.MODE_EXCEL,
    )
    assert int(excel_result["results_count"]) >= 1
    assert int(excel_result.get("skipped_count", 0)) == 0
