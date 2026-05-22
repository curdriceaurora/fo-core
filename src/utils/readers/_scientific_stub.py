"""Stubs for scientific readers when scipy/h5py are not installed.

Imported by utils/readers/__init__.py when the scientific extra is absent
or when the C extensions fail to load (e.g. scipy on Python 3.14).
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


def _unavailable(name: str) -> str:  # pragma: no cover
    return (
        f"{name}: scientific readers unavailable — "
        "install fo-core[scientific] to enable HDF5/MAT/NetCDF support."
    )


def read_hdf5_file(  # pragma: no cover
    file_path: str | Path | None = None,
    max_datasets: int = 20,
    *,
    fileobj: BinaryIO | None = None,
) -> str:
    return _unavailable("HDF5")


def read_mat_file(  # pragma: no cover
    file_path: str | Path | None = None,
    *,
    fileobj: BinaryIO | None = None,
) -> str:
    return _unavailable("MAT")


def read_netcdf_file(  # pragma: no cover
    file_path: str | Path | None = None,
    *,
    fileobj: BinaryIO | None = None,
) -> str:
    return _unavailable("NetCDF")
