# Supported File Types

| Category | Formats | Count |
|----------|---------|-------|
| Documents | `.txt`, `.md`, `.pdf`, `.docx`, `.doc`, `.csv`, `.xlsx`, `.xls`, `.ppt`, `.pptx`, `.epub` | 11 |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.tif` | 7 |
| Video | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv` | 5 |
| Audio | `.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg` | 5 |
| Archives | `.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.rar` | 7 |
| Scientific | `.hdf5`, `.h5`, `.hdf`, `.nc`, `.nc4`, `.netcdf`, `.mat` | 7 |
| CAD | `.dxf`, `.dwg`, `.step`, `.stp`, `.iges`, `.igs` | 6 |

**Total**: 48+ file types supported

## Optional Dependencies

Each format group maps to an install extra:

| Category | Formats | Optional Dependencies | Install Group |
|----------|---------|----------------------|---------------|
| Documents | `.txt`, `.md`, `.pdf`, `.docx`, `.csv`, `.xlsx`, `.pptx` | PyMuPDF, python-docx, openpyxl, python-pptx | Core / none |
| Ebooks | `.epub` | ebooklib | Core |
| Archives | `.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`, `.rar` | py7zr, rarfile | `[archive]` |
| Scientific | `.hdf5`, `.h5`, `.hdf`, `.nc`, `.nc4`, `.netcdf`, `.mat` | h5py, netCDF4, scipy | `[scientific]` |
| CAD | `.dxf`, `.dwg`, `.step`, `.stp`, `.iges`, `.igs` | ezdxf | `[cad]` |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.tif` | None (VisionProcessor) | Core |
| Audio | `.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg` | faster-whisper, torch | `[audio]` |
| Video | `.mp4`, `.avi`, `.mkv`, `.mov`, `.wmv` | opencv-python, scenedetect | `[video]` |

```bash
pip install -e ".[archive]"
pip install -e ".[scientific]"
pip install -e ".[cad]"
pip install -e ".[audio]"
pip install -e ".[video]"
pip install -e ".[all]"
```

## Global Limits

All readers share a maximum file size check before processing:

| Parameter | Default | Location |
|-----------|---------|----------|
| `MAX_FILE_SIZE_BYTES` | 500 MB | `src/utils/readers/_base.py` |

Files exceeding this limit raise `FileTooLargeError` and are skipped.

## Documents

Source module: `src/utils/readers/documents.py`

### Plain Text (`.txt`, `.md`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_chars` | 5000 | Maximum characters read from file |

- Reads with UTF-8 encoding, ignoring decode errors
- No optional dependencies required

### PDF (`.pdf`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_pages` | 5 | Maximum number of pages to extract text from |

- Uses PyMuPDF (`fitz`) for text extraction
- Requires: `PyMuPDF` (included in core dependencies)

### Word Documents (`.docx`)

- Extracts text from all non-empty paragraphs
- Requires: `python-docx` (included in core dependencies)

Only `.docx` (Office Open XML) is supported. Legacy `.doc` (binary format) files are not supported.

### Spreadsheets (`.csv`, `.xlsx`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_rows` | 100 | Maximum rows read from the spreadsheet |

- CSV: built-in `csv` module; Excel: `openpyxl.load_workbook` (`.xlsx` only)
- Requires: `openpyxl` (included in core dependencies)

Only `.xlsx` is supported for Excel files. Legacy `.xls` files are registered but will fail at runtime.

### Presentations (`.pptx`)

- Extracts text from all shapes on each slide
- Requires: `python-pptx` (included in core dependencies)

Only `.pptx` is supported. Legacy `.ppt` files are detected but will fail at runtime.

## Ebooks

Source module: `src/utils/readers/ebook.py`

### EPUB (`.epub`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_chars` | 10000 | Maximum characters extracted from the ebook |

- Strips HTML tags; stops at `max_chars`
- Requires: `ebooklib` (included in core dependencies)

## Archives

Source module: `src/utils/readers/archives.py`

Archive readers extract **metadata and file listings**, not file contents.

### ZIP (`.zip`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_files` | 50 | Maximum number of entries to list |

- Uses Python standard library `zipfile`; no optional dependencies

### 7z (`.7z`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_files` | 50 | Maximum number of entries to list |

- Requires: `py7zr` (`[archive]`)

### TAR Archives (`.tar`, `.tar.gz`, `.tgz`, `.tar.bz2`, `.tbz2`, `.tar.xz`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_files` | 50 | Maximum number of entries to list |

- Uses Python standard library `tarfile`; supports gzip, bzip2, xz

### RAR (`.rar`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_files` | 50 | Maximum number of entries to list |

- Requires: `rarfile` (`[archive]`) and the `unrar` system command

## Scientific Data

Source module: `src/utils/readers/scientific.py`

Scientific readers extract **structure and metadata** rather than raw data arrays.

### HDF5 (`.hdf5`, `.h5`, `.hdf`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_datasets` | 20 | Maximum number of datasets to list |

- Requires: `h5py` (`[scientific]`)

### NetCDF (`.nc`, `.nc4`, `.netcdf`)

- Reports format, dimensions, first 20 variables, global attributes
- Requires: `netCDF4` (`[scientific]`)

### MATLAB (`.mat`)

- Lists first 30 variables with type and shape
- Requires: `scipy` (`[scientific]`)

## CAD

Source module: `src/utils/readers/cad.py`

### DXF (`.dxf`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_layers` | 20 | Maximum number of layers to list |

- Requires: `ezdxf` (`[cad]`)

### DWG (`.dwg`)

- Limited support via `ezdxf`; falls back to basic file info on failure
- Requires: `ezdxf` (`[cad]`)

### STEP (`.step`, `.stp`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_lines` | 100 | Maximum header/data lines to parse |

- Plain text parser; no optional dependencies

### IGES (`.iges`, `.igs`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_lines` | 50 | Maximum lines per section |

- Plain text parser; no optional dependencies

## Reader Dispatch

The `read_file()` function in `src/utils/readers/__init__.py`:

1. Checks file size against `MAX_FILE_SIZE_BYTES` (500 MB)
2. Handles compound extensions (`.tar.gz`, `.tar.bz2`, `.tar.xz`)
3. Maps the extension to the appropriate reader function
4. Returns `None` for unsupported extensions (file is skipped)

If an optional dependency is missing, the reader raises `ImportError` with installation instructions.

## Adding Support for New Formats

1. Create or extend a reader function in `src/utils/readers/`
2. Register the extension in the `readers` mapping inside `read_file()` in `src/utils/readers/__init__.py`
3. If the format requires an optional dependency, add it to `pyproject.toml` under the appropriate install group
4. Use the `_check_file_size()` helper for size validation
5. Raise `FileReadError` on read failures

---
