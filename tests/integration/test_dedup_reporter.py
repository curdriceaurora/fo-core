import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from services.deduplication.reporter import StorageReporter


@pytest.fixture
def sample_duplicate_groups() -> list[dict[str, Any]]:
    return [
        {
            "count": 3,
            "avg_similarity": 0.95,
            "total_size": 3000,
            "representative": "/path/to/rep1.txt",
            "files": ["/path/to/rep1.txt", "/path/to/dup1.txt", "/path/to/dup2.txt"],
        },
        {
            "count": 2,
            "avg_similarity": 1.0,
            "total_size": 4000,
            "representative": "/path/to/rep2.png",
            "files": ["/path/to/rep2.png", "/path/to/dup3.png"],
        },
    ]


@pytest.fixture
def sample_results(sample_duplicate_groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "analyzed_documents": 5,
        "num_groups": 2,
        "space_wasted": 4000,
        "duplicate_groups": sample_duplicate_groups,
    }


def test_storage_reporter_calculate_reclamation(
    sample_duplicate_groups: list[dict[str, Any]],
) -> None:
    reporter = StorageReporter()
    metrics = reporter.calculate_reclamation(sample_duplicate_groups)

    assert metrics["total_duplicate_files"] == 5
    assert metrics["total_duplicate_groups"] == 2
    assert metrics["total_size"] == 7000
    assert metrics["recoverable_space"] == 4000
    assert metrics["recovery_percentage"] == pytest.approx(4000 / 7000 * 100)


def test_storage_reporter_generate_report_json(sample_results: dict[str, Any]) -> None:
    reporter = StorageReporter()
    report = reporter.generate_report(sample_results, output_format="json")
    parsed = json.loads(report)
    assert parsed["num_groups"] == 2
    assert parsed["analyzed_documents"] == 5


def test_storage_reporter_generate_report_text(sample_results: dict[str, Any]) -> None:
    reporter = StorageReporter()
    report = reporter.generate_report(sample_results, output_format="text")
    assert "DOCUMENT DEDUPLICATION REPORT" in report
    assert "Total documents analyzed: 5" in report
    assert "Duplicate groups found: 2" in report
    assert "rep1.txt" in report
    assert "dup3.png" in report


def test_storage_reporter_export_to_csv(
    tmp_path: Path, sample_duplicate_groups: list[dict[str, Any]]
) -> None:
    import csv as csv_module

    reporter = StorageReporter()
    out_file = tmp_path / "report.csv"
    reporter.export_to_csv(sample_duplicate_groups, out_file)

    assert out_file.exists()
    with open(out_file, newline="", encoding="utf-8") as f:
        rows = list(csv_module.reader(f))

    header = rows[0]
    assert header == [
        "Group ID",
        "File Count",
        "Avg Similarity",
        "Total Size (MB)",
        "Representative",
        "All Files",
    ]
    data_rows = rows[1:]
    assert len(data_rows) == len(sample_duplicate_groups)
    # Row 1: representative is "rep1.txt" (index 4), group ID is "1" (index 0)
    assert data_rows[0][0] == "1"
    assert data_rows[0][4] == "rep1.txt"
    # Row 2: representative is "rep2.png"
    assert data_rows[1][4] == "rep2.png"


def test_storage_reporter_export_to_json(tmp_path: Path, sample_results: dict[str, Any]) -> None:
    reporter = StorageReporter()
    out_file = tmp_path / "report.json"
    reporter.export_to_json(sample_results, out_file)

    assert out_file.exists()
    parsed = json.loads(out_file.read_text(encoding="utf-8"))
    assert parsed["num_groups"] == 2
    assert parsed["analyzed_documents"] == 5
    groups = parsed["duplicate_groups"]
    assert len(groups) == 2
    assert groups[0]["representative"] == "/path/to/rep1.txt"
    assert groups[1]["count"] == 2


def test_storage_reporter_export_to_csv_oserror(
    tmp_path: Path, sample_duplicate_groups: list[dict[str, Any]]
) -> None:
    reporter = StorageReporter()
    out_file = tmp_path / "report.csv"
    with (
        patch("builtins.open", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        reporter.export_to_csv(sample_duplicate_groups, out_file)


def test_storage_reporter_export_to_json_oserror(
    tmp_path: Path, sample_results: dict[str, Any]
) -> None:
    reporter = StorageReporter()
    out_file = tmp_path / "report.json"
    with (
        patch("builtins.open", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        reporter.export_to_json(sample_results, out_file)
