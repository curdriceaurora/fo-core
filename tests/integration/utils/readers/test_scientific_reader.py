"""Integration tests for utils/readers/scientific.py.

Covers:
- read_hdf5_file: valid HDF5 with datasets/groups, max_datasets truncation,
  FileReadError on corrupt file, ImportError when h5py unavailable (mocked)
- read_netcdf_file: valid NetCDF with dimensions/variables/attributes,
  FileReadError on corrupt file, ImportError when netCDF4 unavailable (mocked)
- read_mat_file: valid MATLAB v5 mat file, FileReadError on corrupt file,
  ImportError when scipy unavailable (mocked)
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hdf5(tmp_path: Path, name: str = "test.h5") -> Path:
    """Create a minimal HDF5 file with one group and two datasets."""
    h5py = pytest.importorskip("h5py")
    import numpy as np

    path = tmp_path / name
    with h5py.File(path, "w") as f:
        grp = f.create_group("measurements")
        grp.create_dataset("temperature", data=np.array([20.1, 21.5, 19.8]))
        grp.create_dataset("pressure", data=np.array([1013.0, 1012.5]))
        grp["temperature"].attrs["units"] = "Celsius"
    return path


def _make_netcdf(tmp_path: Path, name: str = "test.nc") -> Path:
    """Create a minimal NetCDF4 file with a dimension and variable."""
    netCDF4 = pytest.importorskip("netCDF4")
    import numpy as np

    path = tmp_path / name
    with netCDF4.Dataset(path, "w", format="NETCDF4") as nc:
        nc.createDimension("time", 3)
        nc.createDimension("lat", 4)
        temp = nc.createVariable("temperature", "f4", ("time", "lat"))
        temp[:] = np.ones((3, 4)) * 25.0
        temp.units = "K"
        temp.long_name = "Air Temperature"
        nc.title = "Test dataset"
        nc.institution = "fo-core tests"
    return path


def _make_mat(tmp_path: Path, name: str = "test.mat") -> Path:
    """Create a minimal MATLAB v5 .mat file using scipy."""
    pytest.importorskip("scipy")
    import numpy as np
    from scipy.io import savemat

    path = tmp_path / name
    savemat(str(path), {"x": np.array([1.0, 2.0, 3.0]), "y": np.array([[1, 2], [3, 4]])})
    return path


# ---------------------------------------------------------------------------
# read_hdf5_file
# ---------------------------------------------------------------------------


class TestReadHdf5File:
    @pytest.fixture(autouse=True)
    def _require_h5py(self) -> None:
        pytest.importorskip("h5py")

    def test_valid_hdf5_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hdf5_contains_filename(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path, "mydata.h5")
        result = read_hdf5_file(path)
        assert "mydata.h5" in result

    def test_hdf5_dataset_names_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert "temperature" in result
        assert "pressure" in result

    def test_hdf5_group_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert "measurements" in result

    def test_hdf5_dataset_attributes_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert "units" in result

    def test_hdf5_structure_header_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert "Structure" in result

    def test_hdf5_total_groups_count(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = _make_hdf5(tmp_path)
        result = read_hdf5_file(path)
        assert "Total groups" in result

    def test_hdf5_max_datasets_truncation(self, tmp_path: Path) -> None:
        """max_datasets=1 causes truncation notice."""
        import h5py
        import numpy as np

        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = tmp_path / "many.h5"
        with h5py.File(path, "w") as f:
            for i in range(5):
                f.create_dataset(f"ds{i}", data=np.array([i]))

        result = read_hdf5_file(path, max_datasets=1)
        assert "showing first 1" in result

    def test_hdf5_file_read_error_on_corrupt_file(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.scientific import read_hdf5_file

        path = tmp_path / "corrupt.h5"
        path.write_bytes(b"\x00\x01NOT AN HDF5 FILE\xff\xfe")
        with pytest.raises(FileReadError):
            read_hdf5_file(path)

    def test_hdf5_raises_import_error_when_h5py_unavailable(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import scientific as sci_module

        path = tmp_path / "dummy.h5"
        path.write_bytes(b"placeholder")
        original = sci_module.H5PY_AVAILABLE
        try:
            sci_module.H5PY_AVAILABLE = False
            with pytest.raises(ImportError, match="h5py"):
                sci_module.read_hdf5_file(path)
        finally:
            sci_module.H5PY_AVAILABLE = original


# ---------------------------------------------------------------------------
# read_netcdf_file
# ---------------------------------------------------------------------------


class TestReadNetcdfFile:
    @pytest.fixture(autouse=True)
    def _require_netcdf4(self) -> None:
        pytest.importorskip("netCDF4")

    def test_valid_netcdf_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_netcdf_contains_filename(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path, "climate.nc")
        result = read_netcdf_file(path)
        assert "climate.nc" in result

    def test_netcdf_format_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "Format" in result

    def test_netcdf_dimensions_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "time" in result
        assert "lat" in result

    def test_netcdf_variables_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "temperature" in result

    def test_netcdf_units_attribute_shown(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "units" in result

    def test_netcdf_long_name_attribute_shown(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "long_name" in result

    def test_netcdf_global_attributes_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = _make_netcdf(tmp_path)
        result = read_netcdf_file(path)
        assert "Global Attributes" in result
        assert "title" in result

    def test_netcdf_file_read_error_on_corrupt_file(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.scientific import read_netcdf_file

        path = tmp_path / "corrupt.nc"
        path.write_bytes(b"\x89NOT A NETCDF FILE\x00\x01")
        with pytest.raises(FileReadError):
            read_netcdf_file(path)

    def test_netcdf_raises_import_error_when_unavailable(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import scientific as sci_module

        path = tmp_path / "dummy.nc"
        path.write_bytes(b"placeholder")
        original = sci_module.NETCDF4_AVAILABLE
        try:
            sci_module.NETCDF4_AVAILABLE = False
            with pytest.raises(ImportError, match="netCDF4"):
                sci_module.read_netcdf_file(path)
        finally:
            sci_module.NETCDF4_AVAILABLE = original


# ---------------------------------------------------------------------------
# read_mat_file
# ---------------------------------------------------------------------------


class TestReadMatFile:
    @pytest.fixture(autouse=True)
    def _require_scipy(self) -> None:
        pytest.importorskip("scipy")

    def test_valid_mat_returns_string(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_mat_file

        path = _make_mat(tmp_path)
        result = read_mat_file(path)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mat_contains_filename(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_mat_file

        path = _make_mat(tmp_path, "experiment.mat")
        result = read_mat_file(path)
        assert "experiment.mat" in result

    def test_mat_variables_header_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_mat_file

        path = _make_mat(tmp_path)
        result = read_mat_file(path)
        assert "Variables" in result

    def test_mat_variable_names_listed(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_mat_file

        path = _make_mat(tmp_path)
        result = read_mat_file(path)
        assert "x" in result
        assert "y" in result

    def test_mat_shape_info_present(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers.scientific import read_mat_file

        path = _make_mat(tmp_path)
        result = read_mat_file(path)
        # y is a 2x2 matrix; shape info should appear in output
        assert "2" in result

    def test_mat_file_read_error_on_corrupt_file(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers._base import FileReadError
        from file_organizer.utils.readers.scientific import read_mat_file

        path = tmp_path / "corrupt.mat"
        path.write_bytes(b"\x00\x01NOT A MAT FILE\xff\xfe")
        with pytest.raises(FileReadError):
            read_mat_file(path)

    def test_mat_raises_import_error_when_scipy_unavailable(self, tmp_path: Path) -> None:
        from file_organizer.utils.readers import scientific as sci_module

        path = tmp_path / "dummy.mat"
        path.write_bytes(b"placeholder")
        original = sci_module.SCIPY_AVAILABLE
        try:
            sci_module.SCIPY_AVAILABLE = False
            with pytest.raises(ImportError, match="scipy"):
                sci_module.read_mat_file(path)
        finally:
            sci_module.SCIPY_AVAILABLE = original

    def test_mat_many_variables_truncated(self, tmp_path: Path) -> None:
        """Files with more than 30 variables produce truncation notice."""
        import numpy as np
        from scipy.io import savemat

        from file_organizer.utils.readers.scientific import read_mat_file

        path = tmp_path / "big.mat"
        data = {f"var_{i}": np.array([float(i)]) for i in range(35)}
        savemat(str(path), data)

        result = read_mat_file(path)
        assert "more variables" in result
