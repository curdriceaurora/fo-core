"""Integration tests for utils/readers/cad.py.

Covers:
- read_step_file: valid STEP with full header, no header, empty DATA section,
  entity count, FileReadError on missing file, via read_cad_file dispatcher
- read_iges_file: valid IGES with start/global sections, entity directory entries,
  no structured sections, FileReadError on missing file, via dispatcher
- read_dxf_file: ImportError when ezdxf unavailable (mocked), valid DXF via ezdxf,
  FileReadError on corrupt file
- read_dwg_file: fallback metadata path when ezdxf parse fails
- read_cad_file: unsupported extension raises FileReadError
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(tmp_path: Path, name: str = "test.step") -> Path:
    """Create a minimal valid STEP file."""
    path = tmp_path / name
    content = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('A simple test part'),'2;1');\n"
        "FILE_NAME('test.step','2024-01-01T00:00:00','Author','Org','','','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "#1=PRODUCT('test','test part','',(#2));\n"
        "#2=PRODUCT_CONTEXT('',#3,'mechanical');\n"
        "#3=APPLICATION_CONTEXT('automotive design');\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _make_iges(tmp_path: Path, name: str = "test.igs") -> Path:
    """Create a minimal valid IGES file (fixed-format, 80-char lines)."""

    # IGES fixed-format: column 73 is section type marker
    # S=Start, G=Global, D=Directory, P=Parameter, T=Terminate
    def _pad(line: str, section: str) -> str:
        """Pad line to 72 chars then append section marker + seq number."""
        data = line[:72].ljust(72)
        return data + section

    lines = [
        _pad("Test IGES file generated for testing purposes", "S") + "0000001\n",
        _pad("1H,,1H;,7Htestfile,7Htest.igs,32Hfo-core test,1H ,11,38,6,308,15,", "G")
        + "0000001\n",
        _pad("7Htest.igs,1.,2,2HMM,1,0.01,13H200101.000000,0.0001,1000.,", "G") + "0000002\n",
        _pad("0,0,15HOpen IGES;", "G") + "0000003\n",
        _pad("     110       1       1       0       0       0       0       0", "D") + "0000001\n",
        _pad("     110       0       2    Line Entity", "D") + "0000002\n",
        _pad("110,0.,0.,0.,10.,10.,0.;", "P") + "       10000001\n",
        _pad("S      1G      3D      2P      1", "T") + "0000001\n",
    ]
    path = tmp_path / name
    path.write_text("".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# read_step_file
# ---------------------------------------------------------------------------


class TestReadStepFile:
    def test_valid_step_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_step_contains_filename(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path, "assembly.step")
        result = read_step_file(path)
        assert "assembly.step" in result

    def test_step_header_section_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "Header Information" in result

    def test_step_file_description_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "FILE_DESCRIPTION" in result

    def test_step_file_name_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "FILE_NAME" in result

    def test_step_file_schema_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "FILE_SCHEMA" in result

    def test_step_entity_count_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "entity count" in result.lower()

    def test_step_file_size_shown(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path)
        result = read_step_file(path)
        assert "KB" in result

    def test_step_no_header_section(self, tmp_path: Path) -> None:
        """STEP file without a HEADER; block still returns basic info."""
        from file_organizer.utils.readers.cad import read_step_file

        path = tmp_path / "noheader.step"
        path.write_text("ISO-10303-21;\nDATA;\n#1=DUMMY();\nENDSEC;\nEND-ISO-10303-21;\n")
        result = read_step_file(path)
        assert "STEP File Information" in result
        assert "noheader.step" in result

    def test_step_no_data_section(self, tmp_path: Path) -> None:
        """STEP file with header only — no DATA section."""
        from file_organizer.utils.readers.cad import read_step_file

        path = tmp_path / "nodata.step"
        path.write_text(
            "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'1');\nENDSEC;\nEND-ISO-10303-21;\n"
        )
        result = read_step_file(path)
        assert "STEP File Information" in result

    def test_step_missing_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.cad import read_step_file

        with pytest.raises(FileReadError):
            read_step_file(tmp_path / "nonexistent.step")

    def test_step_accepts_stp_extension(self, tmp_path: Path) -> None:
        """Same content with .stp extension still works."""
        from file_organizer.utils.readers.cad import read_step_file

        path = _make_step(tmp_path, "drawing.stp")
        result = read_step_file(path)
        assert "drawing.stp" in result


# ---------------------------------------------------------------------------
# read_iges_file
# ---------------------------------------------------------------------------


class TestReadIgesFile:
    def test_valid_iges_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_iges_contains_filename(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path, "part.iges")
        result = read_iges_file(path)
        assert "part.iges" in result

    def test_iges_header_information_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert "IGES File Information" in result

    def test_iges_start_section_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert "Start Section" in result

    def test_iges_global_parameters_extracted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert "Global Parameters" in result

    def test_iges_directory_entries_counted(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert "Directory entries found" in result

    def test_iges_file_size_shown(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path)
        result = read_iges_file(path)
        assert "KB" in result

    def test_iges_no_structured_sections(self, tmp_path: Path) -> None:
        """IGES file with lines shorter than 73 chars — no sections detected."""
        from file_organizer.utils.readers.cad import read_iges_file

        path = tmp_path / "short.igs"
        path.write_text("Short line without section marker\n" * 5)
        result = read_iges_file(path)
        assert "IGES File Information" in result
        assert "short.igs" in result

    def test_iges_missing_file_raises_file_read_error(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.cad import read_iges_file

        with pytest.raises(FileReadError):
            read_iges_file(tmp_path / "ghost.iges")

    def test_iges_accepts_iges_extension(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_iges_file

        path = _make_iges(tmp_path, "drawing.iges")
        result = read_iges_file(path)
        assert "drawing.iges" in result


# ---------------------------------------------------------------------------
# read_dxf_file (with ezdxf mocked)
# ---------------------------------------------------------------------------


class TestReadDxfFile:
    def test_raises_import_error_when_ezdxf_unavailable(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "test.dxf"
        path.write_text("placeholder")
        original = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = False
            with pytest.raises(ImportError, match="ezdxf"):
                cad_module.read_dxf_file(path)
        finally:
            cad_module.EZDXF_AVAILABLE = original

    def test_valid_dxf_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module

        if not cad_module.EZDXF_AVAILABLE:
            pytest.skip("ezdxf not installed")

        import ezdxf

        doc = ezdxf.new(dxfversion="R2010")
        msp = doc.modelspace()
        msp.add_line((0, 0), (10, 10))
        dxf_path = tmp_path / "test.dxf"
        doc.saveas(dxf_path)

        result = cad_module.read_dxf_file(dxf_path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_dxf_contains_version(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module

        if not cad_module.EZDXF_AVAILABLE:
            pytest.skip("ezdxf not installed")

        import ezdxf

        doc = ezdxf.new(dxfversion="R2010")
        dxf_path = tmp_path / "version_test.dxf"
        doc.saveas(dxf_path)

        result = cad_module.read_dxf_file(dxf_path)
        assert "DXF Version" in result

    def test_dxf_file_read_error_on_corrupt_file(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module
        from file_organizer.utils.readers._base import FileReadError

        if not cad_module.EZDXF_AVAILABLE:
            pytest.skip("ezdxf not installed")

        path = tmp_path / "corrupt.dxf"
        path.write_bytes(b"\x00\x01\x02INVALID DXF CONTENT\xff\xfe")
        with pytest.raises(FileReadError):
            cad_module.read_dxf_file(path)

    def test_dxf_mocked_ezdxf_success(self, tmp_path: Path) -> None:
        """Exercise read_dxf_file using a fully mocked ezdxf document."""
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "mocked.dxf"
        path.write_text("placeholder")

        mock_layer = MagicMock()
        mock_layer.dxf.name = "0"
        mock_layer.dxf.color = 7

        mock_entity = MagicMock()
        mock_entity.dxftype.return_value = "LINE"

        mock_doc = MagicMock()
        mock_doc.dxfversion = "AC1015"
        mock_doc.header.get.side_effect = lambda key, default="": {
            "$TITLE": "Test Drawing",
            "$AUTHOR": "",
            "$LASTSAVEDBY": "TestUser",
        }.get(key, default)
        mock_doc.layers = [mock_layer]
        mock_doc.modelspace.return_value = [mock_entity]
        mock_doc.blocks = []

        sentinel = object()
        original_ezdxf = getattr(cad_module, "ezdxf", sentinel)
        original = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = True
            cad_module.ezdxf = SimpleNamespace(readfile=MagicMock(return_value=mock_doc))
            result = cad_module.read_dxf_file(path, max_layers=1)
        finally:
            cad_module.EZDXF_AVAILABLE = original
            if original_ezdxf is sentinel:
                del cad_module.ezdxf
            else:
                cad_module.ezdxf = original_ezdxf

        assert "=== DXF Document Metadata ===" in result
        assert "Title: Test Drawing" in result
        assert "Author: TestUser" in result
        assert "=== Layers (1 total) ===" in result
        assert "Layer: 0 (Color: 7)" in result
        assert "=== Entities ===" in result
        assert "Total entities: 1" in result
        assert "LINE: 1" in result

    def test_dxf_mocked_blocks_and_layer_truncation_without_ezdxf_dependency(
        self, tmp_path: Path
    ) -> None:
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "mocked_blocks.dxf"
        path.write_text("placeholder")

        mock_layers = []
        for idx in range(3):
            layer = MagicMock()
            layer.dxf.name = f"Layer{idx}"
            layer.dxf.color = idx + 1
            mock_layers.append(layer)

        mock_entities = [MagicMock(), MagicMock()]
        mock_entities[0].dxftype.return_value = "LINE"
        mock_entities[1].dxftype.return_value = "CIRCLE"

        user_block = MagicMock()
        user_block.name = "TitleBlock"
        internal_block = MagicMock()
        internal_block.name = "*Model_Space"

        mock_doc = MagicMock()
        mock_doc.dxfversion = "AC1018"
        mock_doc.header.get.side_effect = lambda key, default="": {"$TITLE": ""}.get(key, default)
        mock_doc.layers = mock_layers
        mock_doc.modelspace.return_value = mock_entities
        mock_doc.blocks = [user_block, internal_block]

        sentinel = object()
        original_ezdxf = getattr(cad_module, "ezdxf", sentinel)
        original_available = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = True
            cad_module.ezdxf = SimpleNamespace(readfile=MagicMock(return_value=mock_doc))
            result = cad_module.read_dxf_file(path, max_layers=2)
        finally:
            cad_module.EZDXF_AVAILABLE = original_available
            if original_ezdxf is sentinel:
                del cad_module.ezdxf
            else:
                cad_module.ezdxf = original_ezdxf

        assert "DXF Version: AC1018" in result
        assert "... and 1 more layers" in result
        assert "LINE: 1" in result
        assert "CIRCLE: 1" in result
        assert "Block definitions: 1" in result


# ---------------------------------------------------------------------------
# read_dwg_file
# ---------------------------------------------------------------------------


class TestReadDwgFile:
    def test_raises_import_error_when_ezdxf_unavailable(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "test.dwg"
        path.write_bytes(b"AC1015" + b"\x00" * 50)
        original = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = False
            with pytest.raises(ImportError, match="ezdxf"):
                cad_module.read_dwg_file(path)
        finally:
            cad_module.EZDXF_AVAILABLE = original

    def test_dwg_fallback_when_parse_fails(self, tmp_path: Path) -> None:
        """When ezdxf cannot parse the DWG, fallback metadata is returned."""
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "unknown.dwg"
        path.write_bytes(b"NOT A REAL DWG FILE CONTENT")

        sentinel = object()
        original_ezdxf = getattr(cad_module, "ezdxf", sentinel)
        original = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = True
            cad_module.ezdxf = SimpleNamespace(
                readfile=MagicMock(side_effect=Exception("Cannot parse DWG"))
            )
            result = cad_module.read_dwg_file(path)
        finally:
            cad_module.EZDXF_AVAILABLE = original
            if original_ezdxf is sentinel:
                del cad_module.ezdxf
            else:
                cad_module.ezdxf = original_ezdxf

        assert "DWG File Information" in result
        assert "unknown.dwg" in result
        assert "KB" in result

    def test_dwg_fallback_missing_file_raises_file_read_error(self, tmp_path: Path) -> None:
        """Fallback path: file doesn't exist → FileReadError."""
        from file_organizer.utils.readers import cad as cad_module
        from file_organizer.utils.readers._base import FileReadError

        path = tmp_path / "ghost.dwg"

        sentinel = object()
        original_ezdxf = getattr(cad_module, "ezdxf", sentinel)
        original = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = True
            cad_module.ezdxf = SimpleNamespace(
                readfile=MagicMock(side_effect=Exception("Cannot parse"))
            )
            with pytest.raises(FileReadError):
                cad_module.read_dwg_file(path)
        finally:
            cad_module.EZDXF_AVAILABLE = original
            if original_ezdxf is sentinel:
                del cad_module.ezdxf
            else:
                cad_module.ezdxf = original_ezdxf

    def test_dwg_mocked_ezdxf_success_without_dependency(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import cad as cad_module

        path = tmp_path / "mocked.dwg"
        path.write_bytes(b"dwg-placeholder")

        layer = MagicMock()
        layer.dxf.name = "Model"
        layer.dxf.color = 2

        entity = MagicMock()
        entity.dxftype.return_value = "ARC"

        block = MagicMock()
        block.name = "Detail"

        mock_doc = MagicMock()
        mock_doc.dxfversion = "AC1024"
        mock_doc.header.get.side_effect = lambda key, default="": {
            "$TITLE": "DWG Mock",
            "$AUTHOR": "CADUser",
        }.get(key, default)
        mock_doc.layers = [layer]
        mock_doc.modelspace.return_value = [entity]
        mock_doc.blocks = [block]

        sentinel = object()
        original_ezdxf = getattr(cad_module, "ezdxf", sentinel)
        original_available = cad_module.EZDXF_AVAILABLE
        try:
            cad_module.EZDXF_AVAILABLE = True
            cad_module.ezdxf = SimpleNamespace(readfile=MagicMock(return_value=mock_doc))
            result = cad_module.read_dwg_file(path)
        finally:
            cad_module.EZDXF_AVAILABLE = original_available
            if original_ezdxf is sentinel:
                del cad_module.ezdxf
            else:
                cad_module.ezdxf = original_ezdxf

        assert "Title: DWG Mock" in result
        assert "Author: CADUser" in result
        assert "DXF Version: AC1024" in result
        assert "ARC: 1" in result
        assert "Block definitions: 1" in result


# ---------------------------------------------------------------------------
# read_cad_file dispatcher
# ---------------------------------------------------------------------------


class TestReadCadFile:
    def test_dispatcher_routes_step(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_cad_file

        path = _make_step(tmp_path, "dispatch.step")
        result = read_cad_file(path)
        assert "STEP File Information" in result

    def test_dispatcher_routes_stp(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_cad_file

        path = _make_step(tmp_path, "dispatch.stp")
        result = read_cad_file(path)
        assert "STEP File Information" in result

    def test_dispatcher_routes_iges(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_cad_file

        path = _make_iges(tmp_path, "dispatch.iges")
        result = read_cad_file(path)
        assert "IGES File Information" in result

    def test_dispatcher_routes_igs(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.cad import read_cad_file

        path = _make_iges(tmp_path, "dispatch.igs")
        result = read_cad_file(path)
        assert "IGES File Information" in result

    def test_dispatcher_unsupported_extension_raises_file_read_error(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.cad import read_cad_file

        path = tmp_path / "drawing.obj"
        path.write_text("v 0 0 0")
        with pytest.raises(FileReadError, match="Unsupported"):
            read_cad_file(path)
