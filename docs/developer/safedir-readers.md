# SafeDir Reader Contract

This guide covers everything a contributor needs to add a new file reader or migrate
an existing one to the SafeDir primitive. Read it before touching any file-reading
code under `src/`.

## What Problem It Solves

`fo` walks user-pointed directories, reads file contents into an LLM, and moves files
based on AI-derived categories. The realistic attack surface: a symlink dropped into
`~/Downloads` (via browser download, AirDrop, extracted archive) targeting
`~/.ssh/id_rsa` or `~/.aws/credentials`. Without protection, `fo organize` follows
the symlink, reads the credential file, and its contents leave the host through the
inference path even though the move itself looks harmless.

The classic defense — `safe_walk()` filtering symlinks during directory traversal — has
a race window: between the walk yielding a path and a library reader opening it, an
attacker can swap a regular file for a symlink. `SafeDir` closes this window by holding
an open directory fd and opening every file through that fd with `O_NOFOLLOW`, so the
kernel rejects the open if the final component is a symlink — regardless of what
happened after the walk.

See [the hardening epic](#264) and
[`docs/internal/safedir-shutil-audit-pr3j.md`](../internal/safedir-shutil-audit-pr3j.md)
for the full threat model and TOCTOU analysis.

---

## Core API

```python
from utils.safedir import SafeDir, SymlinkRejected
```

### `SafeDir.open_root(path)`

Opens `path` as a trusted root and returns a context manager. Every subsequent
operation resolves through the held directory fd.

```python
with SafeDir.open_root(file_path.parent) as safe_dir:
    ...
```

Raises:
- `NotImplementedError` — on Windows (`O_NOFOLLOW` / `dir_fd=` are POSIX-only)
- `SymlinkRejected` — if `path` itself is a symlink
- `OSError` — if `path` doesn't exist or can't be opened

### `safe_dir.open_for_reader(name) → int`

Opens `name` (a single path component, no separators) read-only with `O_NOFOLLOW`
and returns a raw file descriptor. The fd is the canonical handoff to content readers.

```python
fd = safe_dir.open_for_reader(file_path.name)
```

Raises:
- `ValueError` — if `name` contains `/`, `\`, NUL, or is `""` / `"."` / `".."`
- `SymlinkRejected` — if `name` resolves to a symlink
- `FileNotFoundError` — if `name` doesn't exist
- `OSError` — for other failures (permission, etc.)

### `SymlinkRejected`

A subclass of `OSError`. Catch it **before** broad `except OSError` so symlink
rejections are logged separately from ordinary I/O errors.

### `safe_dir.lstat(name) → os.stat_result`

Stat `name` without following symlinks. Used for inode-pinning (PR4/PR5): capture
`(st_dev, st_ino, st_size)` at decision time, re-check before any destructive op.

### `safe_dir.unlink(name)`

Remove `name` via the held fd (`os.unlink(..., dir_fd=self._fd)`). Use instead of
`Path.unlink()` in any security-sensitive deletion.

---

## Migrating a Reader

### The Minimal Pattern

```python
import os
import sys
from utils.safedir import SafeDir, SymlinkRejected

def extract_text(file_path: Path) -> str:
    if sys.platform != "win32":
        try:
            with SafeDir.open_root(file_path.parent) as safe_dir:
                fd = safe_dir.open_for_reader(file_path.name)
                # fdopen takes ownership only once it returns successfully.
                # Close the bare fd explicitly if fdopen raises, or it leaks.
                try:
                    fileobj = os.fdopen(fd, "rb", closefd=True)
                except OSError:
                    os.close(fd)
                    raise
                with fileobj:
                    return _read_from_fileobj(fileobj, file_path.name)
        except SymlinkRejected as exc:
            logger.warning("Refused symlinked file %s: %s", file_path, exc)
            return ""
        except NotImplementedError:
            logger.debug("SafeDir unavailable; using legacy reader for %s", file_path.name)
        except (OSError, ValueError, ImportError) as e:
            logger.error("Error reading %s: %s", file_path, e)
            return ""

    # Legacy path-based fallback — Windows only.
    return _read_via_path(file_path)
```

The canonical reference is `src/services/deduplication/extractor.py:89–122`.

### Making a Reader Accept `fileobj=`

Readers called from the SafeDir path must accept a binary file-like object rather
than a path string. The standard dual-signature:

```python
def read_my_file(
    file_path: str | Path | None = None,
    *,
    fileobj: BinaryIO | None = None,
) -> str:
    if fileobj is None and file_path is None:
        raise ValueError("read_my_file requires file_path or fileobj")

    if fileobj is not None:
        label = Path(file_path).name if file_path is not None else "<fileobj>"
        _check_fd_size(fileobj)             # enforce the 500 MB cap
        try:
            return _parse(fileobj, label)
        except (OSError, ValueError) as e:
            raise FileReadError(f"Failed to read {label}: {e}") from e

    # Legacy path branch — used only when SafeDir is unavailable.
    assert file_path is not None
    path = Path(file_path)
    _check_file_size(path)
    with path.open("rb") as f:  # safedir: ok — legacy path-branch; SafeDir-aware callers pass fileobj=
        return _parse(f, path.name)
```

`_check_fd_size` is in `src/utils/readers/_base.py`. Use it in the fileobj branch
instead of `path.stat()` — see the [streaming-vs-stat rule](#streaming-vs-stat) below.

The reference for this dual-path pattern is `src/utils/readers/cad.py:read_dxf_file`.

### TextIOWrapper Readers

If the underlying library requires a text stream (e.g. `ezdxf.read(TextIO)`), wrap
the binary fd and **always detach** to prevent the wrapper closing the caller's fd:

```python
text_stream = io.TextIOWrapper(fileobj, encoding="utf-8", errors="surrogateescape")
try:
    doc = library.read(text_stream)
finally:
    text_stream.detach()   # must happen on every exit path, including exceptions
```

Without `detach()`, `TextIOWrapper.__del__` closes the underlying binary stream,
corrupting the caller's fd. The reference is `src/utils/readers/cad.py:_read_dxf_from_fileobj`.

---

## Streaming vs Stat

**Rule: use `os.fstat(fd)` for metadata; never call `path.stat()` before a SafeDir open.**

`O_NOFOLLOW` protects only the final path component. A subsequent `path.stat()` on
the same path re-traverses every intermediate directory, reintroducing the TOCTOU
race that SafeDir was designed to close. Metadata and bytes could belong to different
files.

Once `open_for_reader(name)` returns an fd, `os.fstat(fd)` reads metadata from the
same inode that `read(fd)` will read content from — TOCTOU-free by construction.

```python
# Good — metadata from the already-open fd
try:
    size_kb = os.fstat(fileobj.fileno()).st_size / 1024
except (OSError, AttributeError, ValueError):
    size_kb = -1.0

# Bad — re-traverses the path, introduces a race
size_kb = file_path.stat().st_size / 1024   # never do this before SafeDir open
```

The only acceptable exception: `path.stat()` used **solely for logging / display**,
where the value never influences what bytes are read, what limits are enforced, or
what actions are taken.

See `docs/internal/safedir-shutil-audit-pr3j.md` for the full analysis.

---

## The Opt-Out Marker

When a path-based reader call is intentionally retained (legacy fallback, Windows
path, user-specified output), suppress the lint rail with:

```python
result = some_library.open(path)  # safedir: ok — legacy path-branch; SafeDir-aware callers pass fileobj=
```

**Grammar:**
- Marker must be in a **tokenized comment**, not a string literal.
- Both `—` (em-dash) and `-` (hyphen) are accepted after `ok`.
- A **reason** (at least one non-whitespace character) is required.
- The marker may appear up to **2 lines above** or **6 lines below** the flagged call.

**Accepted reasons** (from `scripts/check_safedir_required.py`):

| Reason | When to use |
|--------|-------------|
| `legacy path-branch; SafeDir-aware callers pass fileobj=` | Path branch in a dual-signature reader |
| `Windows / NotImplementedError fallback` | Platform fallback when SafeDir primitives are unavailable |
| `user output` | One-shot export to a user-supplied path |
| `write-only; atomicity handled by atomic_write helper` | Write path, not a reader |
| `shutil.move target; destination SafeDir-validated by caller` | Move destination already validated upstream |

Reasons not on this list will be questioned in code review. When in doubt, migrate
to `fileobj=` instead of opting out.

---

## Exception Handling Shape

The required shape — in order:

```python
try:
    with SafeDir.open_root(...) as sd:
        fd = sd.open_for_reader(name)
        try:
            fileobj = os.fdopen(fd, "rb", closefd=True)
        except OSError:
            os.close(fd)
            raise
        with fileobj:
            return _read(fileobj)
except SymlinkRejected as exc:          # 1. symlink rejection — security event
    logger.warning("Refused ...: %s", exc)
    return sentinel
except NotImplementedError:             # 2. Windows / POSIX-unavailable — fall through
    pass
except (OSError, ValueError) as e:      # 3. ordinary I/O errors
    logger.error("Error ...: %s", e)
    return sentinel
```

Rules:
1. `SymlinkRejected` **must** be caught before `except OSError` — it is a subclass.
2. `NotImplementedError` **must** be caught before `except Exception` — it is not an
   `OSError` subclass and must not be swallowed as an I/O error.
3. The fd **must** be closed explicitly if `os.fdopen` raises before taking ownership.
4. The `fileobj` context manager **must** be entered inside the `SafeDir` context so
   the file is closed before the directory fd is released.

---

## TextIOWrapper Detach Rail

Any function in `src/utils/readers/` or `src/utils/epub_enhanced.py` that
constructs `io.TextIOWrapper(fileobj, ...)` must call `.detach()` before the
function returns. The wrapper takes close-ownership of the underlying binary
stream by default; failing to detach closes the caller's stream on GC.

```python
# GOOD — ownership explicitly surrendered before function returns
def read_step_file(fileobj: BinaryIO) -> str:
    wrapper = io.TextIOWrapper(fileobj, encoding="utf-8")
    try:
        return wrapper.read()
    finally:
        wrapper.detach()
```

This pattern is enforced by the `textiowrapper-detach` advisory rail
(`scripts/check_textiowrapper_detach.py`, baseline CI test
`tests/ci/test_textiowrapper_detach_rail.py`). Use the opt-out comment
`# textiowrapper-detach: ok — <reason>` on the wrapper-assignment line when the
function intentionally takes ownership of the stream lifecycle.

---

## The Lint Rail

`scripts/check_safedir_required.py` runs as a pre-commit hook and in
`tests/ci/test_symlink_safety_lints.py`. It flags any call to a symlink-following
library function in `src/` that lacks either:

- A SafeDir-based open (detected by presence of `safe_dir.open_for_reader` or
  `open_for_reader` in the surrounding scope), or
- A `# safedir: ok — <reason>` opt-out marker within the window.

**Flagged calls include:** `fitz.open`, `docx.Document`, `openpyxl.load_workbook`,
`pptx.Presentation`, `pypdf.PdfReader`, `Image.open`, `py7zr.SevenZipFile`,
`rarfile.RarFile`, `tarfile.open`, `zipfile.ZipFile`, `shutil.copy`, `shutil.copy2`,
`shutil.copyfile`, `shutil.copytree`, `shutil.move`.

Bare `open()` / `Path.open()` / `io.open()` in read mode are also flagged in the
directories that have been migrated to SafeDir (listed in `_READ_OPEN_ENFORCED_DIRS`).

---

## Cross-References

- `src/utils/safedir.py` — primitive implementation and full API docs
- `scripts/check_safedir_required.py` — lint rail implementation
- `tests/ci/test_symlink_safety_lints.py` — rail correctness tests
- `docs/internal/safedir-shutil-audit-pr3j.md` — streaming-vs-stat audit and
  call-site migration record (PR3a–PR3j)
- Issue [#264](https://github.com/curdriceaurora/fo-core/issues/264) — hardening epic
  and threat model
