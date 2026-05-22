"""Stubs for scientific readers when scipy/h5py are not installed.

Imported by utils/readers/__init__.py when the scientific extra is absent
or when the C extensions fail to load (e.g. scipy on Python 3.14).
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


def _unavailable(name: str) -> str:
    return (
        f"{name}: scientific readers unavailable — "
        "install fo-core[scientific] to enable HDF5/MAT/NetCDF support."
    )


def read_hdf5_file(
    file_path: Path | None = None,
    fileobj: BinaryIO | None = None,
    max_datasets: int = 10,
) -> str:
    return _unavailable("HDF5")


def read_mat_file(
    file_path: Path | None = None,
    fileobj: BinaryIO | None = None,
) -> str:
    return _unavailable("MAT")


def read_netcdf_file(
    file_path: Path | None = None,
    fileobj: BinaryIO | None = None,
    max_variables: int = 10,
) -> str:
    return _unavailable("NetCDF")
