import json

import pytest

from file_organizer.services.deduplication.reporter import StorageReporter


@pytest.fixture
def sample_duplicate_groups():
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
def sample_results(sample_duplicate_groups):
    return {
        "analyzed_documents": 5,
        "num_groups": 2,
        "space_wasted": 4000,
        "duplicate_groups": sample_duplicate_groups,
    }


def test_storage_reporter_calculate_reclamation(sample_duplicate_groups):
    reporter = StorageReporter()
    metrics = reporter.calculate_reclamation(sample_duplicate_groups)

    assert metrics["total_duplicate_files"] == 5
    assert metrics["total_duplicate_groups"] == 2
    assert metrics["total_size"] == 7000
    assert metrics["recoverable_space"] == 4000
    assert metrics["recovery_percentage"] == (4000 / 7000 * 100)


def test_storage_reporter_generate_report_json(sample_results):
    reporter = StorageReporter()
    report = reporter.generate_report(sample_results, output_format="json")
    parsed = json.loads(report)
    assert parsed["num_groups"] == 2
    assert parsed["analyzed_documents"] == 5


def test_storage_reporter_generate_report_text(sample_results):
    reporter = StorageReporter()
    report = reporter.generate_report(sample_results, output_format="text")
    assert "DOCUMENT DEDUPLICATION REPORT" in report
    assert "Total documents analyzed: 5" in report
    assert "Duplicate groups found: 2" in report
    assert "rep1.txt" in report
    assert "dup3.png" in report


def test_storage_reporter_export_to_csv(tmp_path, sample_duplicate_groups):
    reporter = StorageReporter()
    out_file = tmp_path / "report.csv"
    reporter.export_to_csv(sample_duplicate_groups, out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "Group ID,File Count,Avg Similarity" in content
    assert "rep1.txt" in content


def test_storage_reporter_export_to_json(tmp_path, sample_results):
    reporter = StorageReporter()
    out_file = tmp_path / "report.json"
    reporter.export_to_json(sample_results, out_file)

    assert out_file.exists()
    parsed = json.loads(out_file.read_text(encoding="utf-8"))
    assert parsed["num_groups"] == 2
