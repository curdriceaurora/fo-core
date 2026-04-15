"""Smoke canary for the [scientific] optional extra (h5py, netCDF4, scipy)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_scientific_reads_hdf5_file(tmp_path: Path) -> None:
    h5py = pytest.importorskip("h5py")
    import numpy as np  # numpy is a transitive dep of h5py

    from file_organizer.utils.readers.scientific import read_hdf5_file

    # Create a minimal HDF5 file with one dataset and one attribute
    hdf5_path = tmp_path / "test.h5"
    with h5py.File(hdf5_path, "w") as f:
        f.create_dataset("measurements", data=np.array([1.0, 2.5, 3.7]))
        f.attrs["description"] = "canary dataset"

    # read_hdf5_file returns HDF5 structure metadata, not raw dataset values
    result = read_hdf5_file(hdf5_path)

    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.smoke
def test_scipy_importable() -> None:
    pytest.importorskip("scipy")


@pytest.mark.smoke
def test_netcdf4_importable() -> None:
    pytest.importorskip("netCDF4")
