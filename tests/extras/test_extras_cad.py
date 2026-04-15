"""Smoke canary for the [cad] optional extra (ezdxf)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_cad_reads_dxf_file(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    from file_organizer.utils.readers.cad import read_dxf_file

    # Create a minimal DXF file with a single line entity
    dxf_path = tmp_path / "test.dxf"
    doc = ezdxf.new(dxfversion="R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 10))
    doc.saveas(dxf_path)

    # read_dxf_file returns metadata + layer information, not raw DXF text
    result = read_dxf_file(dxf_path)

    assert isinstance(result, str)
    assert "LINE" in result  # the added LINE entity must appear in entity metadata
