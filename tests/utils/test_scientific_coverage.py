"""Coverage tests for scientific readers -- targets uncovered branches."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.readers._base import FileReadError

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# read_hdf5_file
# ---------------------------------------------------------------------------


class TestReadHdf5:
    def test_no_h5py_raises(self):
        with patch("utils.readers.scientific.H5PY_AVAILABLE", False):
            from utils.readers.scientific import read_hdf5_file

            with pytest.raises(ImportError, match="h5py"):
                read_hdf5_file(Path("/test.h5"))

    def test_file_error_raises(self):
        with patch("utils.readers.scientific.H5PY_AVAILABLE", True):
            mock_h5py = MagicMock()
            mock_h5py.File.side_effect = RuntimeError("bad file")
            with patch(
                "utils.readers.scientific.h5py",
                mock_h5py,
                create=True,
            ):
                from utils.readers.scientific import read_hdf5_file

                with pytest.raises(FileReadError):
                    read_hdf5_file(Path("/test.h5"))

    def test_missing_args_raises_value_error(self):
        """Neither file_path nor fileobj supplied — raises ValueError (lines 122-123)."""
        from utils.readers.scientific import read_hdf5_file

        with pytest.raises(ValueError, match="read_hdf5_file requires"):
            read_hdf5_file()

    def test_successful_read_via_path(self):
        """Path branch: _parse_hdf5 called via file_path (lines 59-92, 136-141)."""
        mock_h5py = MagicMock()

        # Build a fake HDF5 file context manager
        mock_hf = MagicMock()
        mock_hf.keys.return_value = ["group1", "ds1"]

        mock_dataset = MagicMock()
        mock_dataset.shape = (10, 5)
        mock_dataset.dtype = "float32"
        mock_dataset.nbytes = 2048
        mock_dataset.attrs.items.return_value = [("units", "m/s"), ("desc", "speed")]
        mock_dataset.attrs.__bool__ = lambda self: True

        mock_h5py.Dataset = type("Dataset", (), {})
        mock_h5py.Group = type("Group", (), {})

        # Make isinstance checks work: mock_dataset is an h5py.Dataset instance
        real_dataset_cls = mock_h5py.Dataset
        real_group_cls = mock_h5py.Group

        def make_dataset():
            d = real_dataset_cls()
            d.shape = (10, 5)
            d.dtype = "float32"
            d.nbytes = 2048
            d.attrs = MagicMock()
            d.attrs.items.return_value = [("units", "m/s")]
            d.attrs.__bool__ = lambda self: True
            return d

        def make_group():
            g = real_group_cls()
            return g

        fake_ds = make_dataset()
        fake_grp = make_group()

        def visititems(callback):
            callback("group1/subgroup", fake_grp)
            callback("group1/data", fake_ds)

        mock_hf.visititems = visititems
        mock_hf.__enter__ = MagicMock(return_value=mock_hf)
        mock_hf.__exit__ = MagicMock(return_value=False)
        mock_h5py.File.return_value = mock_hf

        with (
            patch("utils.readers.scientific.H5PY_AVAILABLE", True),
            patch("utils.readers.scientific.h5py", mock_h5py, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_hdf5_file

            result = read_hdf5_file(Path("/test.h5"))
            assert "HDF5 File" in result

    def test_fileobj_branch_success(self):
        """fileobj branch: calls _parse_hdf5 with fileobj (lines 127-134)."""
        mock_h5py = MagicMock()

        mock_hf = MagicMock()
        mock_hf.keys.return_value = []

        def visititems(callback):
            pass

        mock_hf.visititems = visititems
        mock_hf.__enter__ = MagicMock(return_value=mock_hf)
        mock_hf.__exit__ = MagicMock(return_value=False)
        mock_h5py.File.return_value = mock_hf

        fake_fileobj = io.BytesIO(b"\x89HDF\r\n\x1a\n" + b"\x00" * 512)

        with (
            patch("utils.readers.scientific.H5PY_AVAILABLE", True),
            patch("utils.readers.scientific.h5py", mock_h5py, create=True),
        ):
            from utils.readers.scientific import read_hdf5_file

            result = read_hdf5_file(fileobj=fake_fileobj)
            assert "HDF5 File" in result
            mock_h5py.File.assert_called_once()

    def test_fileobj_branch_with_file_path_label(self):
        """fileobj branch with file_path for label (line 128)."""
        mock_h5py = MagicMock()

        mock_hf = MagicMock()
        mock_hf.keys.return_value = ["a"]

        def visititems(callback):
            pass

        mock_hf.visititems = visititems
        mock_hf.__enter__ = MagicMock(return_value=mock_hf)
        mock_hf.__exit__ = MagicMock(return_value=False)
        mock_h5py.File.return_value = mock_hf

        fake_fileobj = io.BytesIO(b"\x00" * 128)

        with (
            patch("utils.readers.scientific.H5PY_AVAILABLE", True),
            patch("utils.readers.scientific.h5py", mock_h5py, create=True),
        ):
            from utils.readers.scientific import read_hdf5_file

            result = read_hdf5_file(file_path=Path("/data/myfile.h5"), fileobj=fake_fileobj)
            # Label should include the filename
            assert "myfile.h5" in result

    def test_fileobj_branch_error_raises(self):
        """fileobj branch raises FileReadError when _parse_hdf5 throws (line 134)."""
        mock_h5py = MagicMock()
        mock_h5py.File.side_effect = RuntimeError("corrupt")

        fake_fileobj = io.BytesIO(b"\x00" * 128)

        with (
            patch("utils.readers.scientific.H5PY_AVAILABLE", True),
            patch("utils.readers.scientific.h5py", mock_h5py, create=True),
        ):
            from utils.readers.scientific import read_hdf5_file

            with pytest.raises(FileReadError, match="Failed to read HDF5"):
                read_hdf5_file(fileobj=fake_fileobj)

    def test_parse_hdf5_dataset_count_cap(self):
        """Lines 69, 87-88: max_datasets cap with truncation message."""
        mock_h5py = MagicMock()

        dataset_cls = type("Dataset", (), {})
        group_cls = type("Group", (), {})
        mock_h5py.Dataset = dataset_cls
        mock_h5py.Group = group_cls

        mock_hf = MagicMock()
        mock_hf.keys.return_value = [f"ds{i}" for i in range(25)]

        # Create 25 fake Dataset instances
        def make_ds(name):
            d = dataset_cls()
            d.shape = (5,)
            d.dtype = "float64"
            d.nbytes = 40
            d.attrs = MagicMock()
            d.attrs.__bool__ = lambda self: False
            d.attrs.items.return_value = []
            return d

        def visititems(callback):
            for i in range(25):
                callback(f"ds{i}", make_ds(f"ds{i}"))

        mock_hf.visititems = visititems
        mock_hf.__enter__ = MagicMock(return_value=mock_hf)
        mock_hf.__exit__ = MagicMock(return_value=False)
        mock_h5py.File.return_value = mock_hf

        with (
            patch("utils.readers.scientific.H5PY_AVAILABLE", True),
            patch("utils.readers.scientific.h5py", mock_h5py, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_hdf5_file

            result = read_hdf5_file(Path("/test.h5"), max_datasets=20)
            assert "showing first 20 datasets" in result


# ---------------------------------------------------------------------------
# read_netcdf_file
# ---------------------------------------------------------------------------


class TestReadNetcdf:
    def test_no_netcdf4_raises(self):
        with patch("utils.readers.scientific.NETCDF4_AVAILABLE", False):
            from utils.readers.scientific import read_netcdf_file

            with pytest.raises(ImportError, match="netCDF4"):
                read_netcdf_file(Path("/test.nc"))

    def test_file_error_raises(self):
        with patch("utils.readers.scientific.NETCDF4_AVAILABLE", True):
            mock_nc = MagicMock()
            mock_nc.Dataset.side_effect = RuntimeError("bad file")
            with patch(
                "utils.readers.scientific.netCDF4",
                mock_nc,
                create=True,
            ):
                from utils.readers.scientific import read_netcdf_file

                with pytest.raises(FileReadError):
                    read_netcdf_file(Path("/test.nc"))

    def test_missing_args_raises_value_error(self):
        """Neither file_path nor fileobj supplied — raises ValueError (line 217)."""
        from utils.readers.scientific import read_netcdf_file

        with pytest.raises(ValueError, match="read_netcdf_file requires"):
            read_netcdf_file()

    def _make_mock_nc(self, n_vars=3, n_dims=2, n_attrs=2):
        """Return a fully-configured mock netCDF4.Dataset-like object."""
        mock_nc = MagicMock()
        mock_nc.data_model = "NETCDF4"

        # Dimensions
        dim_items = {}
        for i in range(n_dims):
            mock_dim = MagicMock()
            mock_dim.isunlimited.return_value = False
            mock_dim.__len__ = MagicMock(return_value=10 + i)
            dim_items[f"dim{i}"] = mock_dim
        mock_nc.dimensions = dim_items

        # Variables
        var_items = {}
        for i in range(n_vars):
            mock_var = MagicMock(spec=["dtype", "shape", "units", "long_name"])
            mock_var.dtype = "float32"
            mock_var.shape = (10, 5)
            var_items[f"var{i}"] = mock_var
        mock_nc.variables = var_items

        # Attributes
        attr_names = [f"attr{i}" for i in range(n_attrs)]
        mock_nc.ncattrs.return_value = attr_names
        mock_nc.getncattr.side_effect = lambda name: f"value_of_{name}"

        return mock_nc

    def test_successful_read_via_path(self):
        """Path branch: _parse_netcdf called via file_path (lines 156-188, 239-243)."""
        mock_nc_module = MagicMock()
        mock_dataset_instance = self._make_mock_nc(n_vars=3)
        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(
            return_value=mock_dataset_instance
        )
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(Path("/test.nc"))
            assert "NetCDF File" in result
            assert "NETCDF4" in result

    def test_fileobj_branch_success(self):
        """fileobj branch: reads bytes and uses memory= param (lines 221-231)."""
        mock_nc_module = MagicMock()
        mock_dataset_instance = self._make_mock_nc(n_vars=3)
        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(
            return_value=mock_dataset_instance
        )
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        fake_fileobj = io.BytesIO(b"CDF\x01" + b"\x00" * 128)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(fileobj=fake_fileobj)
            assert "NetCDF File" in result
            # Verify Dataset called with memory= kwarg
            call_kwargs = mock_nc_module.Dataset.call_args
            assert "memory" in call_kwargs.kwargs

    def test_fileobj_branch_with_file_path_label(self):
        """fileobj branch uses file_path for the label (line 222)."""
        mock_nc_module = MagicMock()
        mock_dataset_instance = self._make_mock_nc(n_vars=2)
        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(
            return_value=mock_dataset_instance
        )
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        fake_fileobj = io.BytesIO(b"\x00" * 64)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(file_path=Path("/data/climate.nc"), fileobj=fake_fileobj)
            assert "climate.nc" in result

    def test_fileobj_branch_error_raises(self):
        """fileobj branch raises FileReadError when Dataset constructor throws (line 232)."""
        mock_nc_module = MagicMock()
        mock_nc_module.Dataset.side_effect = RuntimeError("corrupt data")

        fake_fileobj = io.BytesIO(b"\x00" * 64)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
        ):
            from utils.readers.scientific import read_netcdf_file

            with pytest.raises(FileReadError, match="Failed to read NetCDF"):
                read_netcdf_file(fileobj=fake_fileobj)

    def test_parse_netcdf_variable_truncation(self):
        """Line 177-178: truncation message when > 20 variables."""
        mock_nc_module = MagicMock()
        # 25 variables — 5 more than the cap of 20
        mock_dataset_instance = self._make_mock_nc(n_vars=25)
        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(
            return_value=mock_dataset_instance
        )
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(Path("/test.nc"))
            assert "... and 5 more variables" in result

    def test_parse_netcdf_unlimited_dimension(self):
        """Line 163: unlimited dimension displays 'unlimited' instead of a number."""
        mock_nc_module = MagicMock()

        mock_nc = MagicMock()
        mock_nc.data_model = "NETCDF4"

        unlimited_dim = MagicMock()
        unlimited_dim.isunlimited.return_value = True
        unlimited_dim.__len__ = MagicMock(return_value=0)

        mock_nc.dimensions = {"time": unlimited_dim}
        mock_nc.variables = {}
        mock_nc.ncattrs.return_value = []

        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(return_value=mock_nc)
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(Path("/test.nc"))
            assert "unlimited" in result

    def test_parse_netcdf_global_attributes(self):
        """Lines 180-184: global attributes section rendered."""
        mock_nc_module = MagicMock()
        mock_dataset_instance = self._make_mock_nc(n_vars=1, n_attrs=3)
        mock_nc_module.Dataset.return_value.__enter__ = MagicMock(
            return_value=mock_dataset_instance
        )
        mock_nc_module.Dataset.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("utils.readers.scientific.NETCDF4_AVAILABLE", True),
            patch("utils.readers.scientific.netCDF4", mock_nc_module, create=True),
            patch("utils.readers._base._check_file_size"),
        ):
            from utils.readers.scientific import read_netcdf_file

            result = read_netcdf_file(Path("/test.nc"))
            assert "Global Attributes" in result


# ---------------------------------------------------------------------------
# read_mat_file
# ---------------------------------------------------------------------------


class TestReadMat:
    def test_no_scipy_raises(self):
        with patch("utils.readers.scientific.SCIPY_AVAILABLE", False):
            from utils.readers.scientific import read_mat_file

            with pytest.raises(ImportError, match="scipy"):
                read_mat_file(Path("/test.mat"))

    def test_file_error_raises(self):
        with patch("utils.readers.scientific.SCIPY_AVAILABLE", True):
            mock_loadmat = MagicMock(side_effect=RuntimeError("bad file"))
            with patch(
                "utils.readers.scientific.loadmat",
                mock_loadmat,
                create=True,
            ):
                from utils.readers.scientific import read_mat_file

                with pytest.raises(FileReadError):
                    read_mat_file(Path("/test.mat"))

    def test_successful_read(self):
        # Use a lightweight fake instead of numpy to avoid an undeclared dependency.
        # A real class named ``ndarray`` avoids mutating the global MagicMock type.
        class ndarray:
            """Fake numpy ndarray for testing."""

            def __init__(self, shape: tuple[int, ...]) -> None:
                self.shape = shape

        mock_matrix = ndarray((2, 2))

        with patch("utils.readers.scientific.SCIPY_AVAILABLE", True):
            mock_data = {
                "__header__": b"test",
                "__version__": "1.0",
                "matrix": mock_matrix,
                "scalar": 42,
            }
            mock_loadmat = MagicMock(return_value=mock_data)
            with patch(
                "utils.readers.scientific.loadmat",
                mock_loadmat,
                create=True,
            ):
                from utils.readers.scientific import read_mat_file

                result = read_mat_file(Path("/test.mat"))
                assert "MATLAB File" in result
                assert "matrix" in result
                assert "scalar" in result

    def test_missing_args_raises_value_error(self):
        """Neither file_path nor fileobj supplied — raises ValueError (line 302)."""
        from utils.readers.scientific import read_mat_file

        with pytest.raises(ValueError, match="read_mat_file requires"):
            read_mat_file()

    def test_mat_variable_truncation(self):
        """Lines 271-272: truncation message when > 30 variables."""
        # Build 35 fake variables (keys not starting with __)
        mock_data: dict = {f"var{i:03d}": 42 for i in range(35)}
        mock_data["__header__"] = b"MATLAB 5.0"
        mock_data["__version__"] = "1.0"
        mock_data["__globals__"] = []

        with patch("utils.readers.scientific.SCIPY_AVAILABLE", True):
            mock_loadmat = MagicMock(return_value=mock_data)
            with patch(
                "utils.readers.scientific.loadmat",
                mock_loadmat,
                create=True,
            ):
                from utils.readers.scientific import read_mat_file

                result = read_mat_file(Path("/test.mat"))
                assert "... and 5 more variables" in result

    def test_fileobj_branch_success(self):
        """fileobj branch: _parse_mat called with fileobj (lines 306-312)."""
        mock_data = {
            "__header__": b"MATLAB 5.0",
            "__version__": "1.0",
            "x": 1.0,
            "y": 2.0,
        }

        fake_fileobj = io.BytesIO(b"\x00" * 64)

        with (
            patch("utils.readers.scientific.SCIPY_AVAILABLE", True),
            patch("utils.readers.scientific.loadmat", MagicMock(return_value=mock_data)),
        ):
            from utils.readers.scientific import read_mat_file

            result = read_mat_file(fileobj=fake_fileobj)
            assert "MATLAB File" in result
            assert "x" in result

    def test_fileobj_branch_with_file_path_label(self):
        """fileobj branch uses file_path for the log label (line 307)."""
        mock_data = {
            "__header__": b"MATLAB 5.0",
            "matrix": 99,
        }

        fake_fileobj = io.BytesIO(b"\x00" * 64)

        with (
            patch("utils.readers.scientific.SCIPY_AVAILABLE", True),
            patch("utils.readers.scientific.loadmat", MagicMock(return_value=mock_data)),
        ):
            from utils.readers.scientific import read_mat_file

            result = read_mat_file(file_path=Path("/data/results.mat"), fileobj=fake_fileobj)
            assert "results.mat" in result

    def test_fileobj_branch_error_raises(self):
        """fileobj branch raises FileReadError when loadmat throws (line 312)."""
        fake_fileobj = io.BytesIO(b"\x00" * 64)

        with (
            patch("utils.readers.scientific.SCIPY_AVAILABLE", True),
            patch(
                "utils.readers.scientific.loadmat",
                MagicMock(side_effect=RuntimeError("parse error")),
            ),
        ):
            from utils.readers.scientific import read_mat_file

            with pytest.raises(FileReadError, match="Failed to read MAT"):
                read_mat_file(fileobj=fake_fileobj)


# ---------------------------------------------------------------------------
# Module-level ImportError branches (lines 30-31, 37-38, 44-45)
# These are the ``except ImportError`` clauses for h5py, netCDF4, scipy.
# We exercise them by importing the module under a patched sys.modules that
# makes the libraries appear absent.
# ---------------------------------------------------------------------------


class TestImportErrorBranches:
    """Cover the ``except ImportError: H5PY_AVAILABLE = False`` branches."""

    def test_h5py_import_error_branch(self):
        """Line 30-31: H5PY_AVAILABLE = False set when h5py absent."""
        import sys
        from unittest.mock import patch as _patch

        with _patch.dict(sys.modules, {"h5py": None}):
            # Force re-execution of the module's try/except block
            import importlib

            import utils.readers.scientific as sci_mod

            importlib.reload(sci_mod)
            assert sci_mod.H5PY_AVAILABLE is False

    def test_netcdf4_import_error_branch(self):
        """Lines 37-38: NETCDF4_AVAILABLE = False set when netCDF4 absent."""
        import sys
        from unittest.mock import patch as _patch

        with _patch.dict(sys.modules, {"netCDF4": None}):
            import importlib

            import utils.readers.scientific as sci_mod

            importlib.reload(sci_mod)
            assert sci_mod.NETCDF4_AVAILABLE is False

    def test_scipy_import_error_branch(self):
        """Lines 44-45: SCIPY_AVAILABLE = False set when scipy absent."""
        import sys
        from unittest.mock import patch as _patch

        with _patch.dict(
            sys.modules,
            {"scipy": None, "scipy.io": None},
        ):
            import importlib

            import utils.readers.scientific as sci_mod

            importlib.reload(sci_mod)
            assert sci_mod.SCIPY_AVAILABLE is False
