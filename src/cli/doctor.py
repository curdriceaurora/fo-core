# pyre-ignore-all-errors
"""Doctor command for detecting file types and recommending optional dependencies.

This module provides functionality to scan directories for file types and recommend
which optional dependency groups should be installed based on detected file types.
"""

import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from cli.interactive import confirm_action
from cli.state import _get_state
from core.path_guard import safe_walk

logger = logging.getLogger(__name__)
console = Console()

_INSTALL_TIMEOUT_SECONDS = 300

# Extension-to-group registry mapping file extensions to optional dependency groups
EXTENSION_REGISTRY: dict[str, str] = {
    # Audio files
    ".mp3": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".m4a": "audio",
    ".wma": "audio",
    ".aac": "audio",
    ".opus": "audio",
    # Video files
    ".mp4": "video",
    ".avi": "video",
    ".mkv": "video",
    ".mov": "video",
    ".wmv": "video",
    ".webm": "video",
    # Document parsers
    ".pdf": "parsers",
    ".docx": "parsers",
    ".xlsx": "parsers",
    ".pptx": "parsers",
    ".epub": "parsers",
    ".html": "parsers",
    # Archive files
    ".7z": "archive",
    ".rar": "archive",
    ".tar.gz": "archive",
    ".tar.bz2": "archive",
    # Scientific data files
    ".hdf5": "scientific",
    ".h5": "scientific",
    ".nc": "scientific",
    ".mat": "scientific",
    # CAD files
    ".dxf": "cad",
    ".dwg": "cad",
}

# Dependency check packages - maps groups to representative packages to check
DEPENDENCY_CHECK_PACKAGES: dict[str, str] = {
    "audio": "faster_whisper",
    "video": "cv2",
    "parsers": "fitz",
    "archive": "py7zr",
    "scientific": "h5py",
    "cad": "ezdxf",
    "dedup": "imagededup",
}

# System prerequisites for optional groups
SYSTEM_PREREQUISITES: dict[str, list[str]] = {
    "audio": ["FFmpeg (required)", "CUDA GPU (optional, for acceleration)"],
    "archive": ["unrar tool (required for RAR files)"],
}

# Group-to-extra mapping: maps internal group names to pyproject.toml extra names.
# Audio and video are bundled under "media", archive under "media" as well (for py7zr).
# Note: "parsers" is not an extra — those packages are core dependencies.
GROUP_TO_EXTRA: dict[str, str] = {
    "audio": "media",
    "video": "media",
    "archive": "media",  # py7zr is in media extra
    "scientific": "scientific",
    "cad": "cad",
    "dedup": "dedup-image",  # imagededup extra
    # "parsers" intentionally omitted — not an extra, core deps
}

# Known problematic combinations that should emit warnings instead of install commands
KNOWN_BROKEN_COMBOS: dict[str, str] = {
    "dedup-image-py314+": (
        "imagededup does not build on Python 3.14+ (arm64 linker error). "
        "This will be fixed in a future release."
    ),
}


def _detect_install_method() -> str:
    """Detect whether fo was installed via pipx or pip.

    Returns:
        "pipx" if running in a pipx venv, "pip" otherwise
    """
    exe_path = sys.executable
    # Check PIPX_HOME env var first (user-configured or set by pipx itself)
    pipx_home = os.environ.get("PIPX_HOME")
    if pipx_home:
        if exe_path.startswith(os.path.join(pipx_home, "venvs") + os.sep):
            return "pipx"
    # Check both the old (~/.local/pipx) and new (~/.local/share/pipx) default locations
    for base in ("~/.local/pipx/venvs/", "~/.local/share/pipx/venvs/"):
        if exe_path.startswith(os.path.expanduser(base)):
            return "pipx"
    return "pip"


def _is_broken_combo(group: str) -> str | None:
    """Check if a group has known installation issues.

    Args:
        group: The dependency group name

    Returns:
        Warning message if broken, None otherwise
    """
    # Check for dedup-image on Python 3.14+
    if group == "dedup" and sys.version_info >= (3, 14):
        return KNOWN_BROKEN_COMBOS["dedup-image-py314+"]
    return None


def _get_extra_name(group: str) -> str | None:
    """Get the canonical extra name for a group.

    Args:
        group: The internal group name (e.g., "audio", "video")

    Returns:
        The extra name from pyproject.toml (e.g., "media"), or None if not an extra
    """
    return GROUP_TO_EXTRA.get(group)


def _groups_to_extras(groups: set[str]) -> list[str]:
    """Convert group names to unique extra names.

    Args:
        groups: Set of internal group names

    Returns:
        Sorted list of unique extra names, filtering out None and broken combos
    """
    extras = set()
    for group in groups:
        extra = _get_extra_name(group)
        if extra and not _is_broken_combo(group):
            extras.add(extra)
    return sorted(extras)


def is_group_installed(group: str) -> bool:
    """Check if an optional dependency group is installed.

    Uses importlib.util.find_spec() for non-destructive checking.

    Args:
        group: The name of the optional dependency group

    Returns:
        True if the group's representative package is installed, False otherwise
    """
    package_name = DEPENDENCY_CHECK_PACKAGES.get(group)
    if not package_name:
        return False

    try:
        spec = importlib.util.find_spec(package_name)
    except (ModuleNotFoundError, ValueError) as exc:
        logger.debug("find_spec(%r) failed: %s", package_name, exc)
        return False
    return spec is not None


def get_groups_for_extensions(extensions: set[str]) -> set[str]:
    """Get the set of dependency groups needed for the given file extensions.

    Args:
        extensions: Set of file extensions (with leading dot, e.g., '.mp3')

    Returns:
        Set of dependency group names
    """
    groups = set()
    for ext in extensions:
        # Normalize extension to lowercase
        ext_lower = ext.lower()
        if ext_lower in EXTENSION_REGISTRY:
            groups.add(EXTENSION_REGISTRY[ext_lower])
    return groups


def get_missing_groups(detected_groups: set[str]) -> set[str]:
    """Filter detected groups to only those not already installed.

    Args:
        detected_groups: Set of detected dependency group names

    Returns:
        Set of group names that are not installed
    """
    return {group for group in detected_groups if not is_group_installed(group)}


def _normalized_extension(path: Path) -> str:
    """Return a normalized extension, preserving supported compound archives.

    Args:
        path: File path

    Returns:
        Normalized extension with leading dot, or empty string if no extension
    """
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if len(suffixes) >= 2:
        compound = "".join(suffixes[-2:])
        if compound in {".tar.gz", ".tar.bz2"}:
            return compound
    return suffixes[-1] if suffixes else ""


def scan_directory(directory: Path) -> dict[str, int]:
    """Scan a directory and count files by extension.

    Skips symlinks, hidden files, and entries that raise filesystem errors
    (e.g. permission denied).  Continues scanning on per-entry failures.

    Args:
        directory: Path to directory to scan

    Returns:
        Dictionary mapping file extensions to counts
    """
    extension_counts: dict[str, int] = {}

    # safe_walk already skips symlinks, hidden entries, and per-entry OSError
    # (PermissionError, stale handles). No additional try/except needed —
    # every yielded path is a readable, regular file.
    for item in safe_walk(directory):
        ext = _normalized_extension(item)
        if ext:
            extension_counts[ext] = extension_counts.get(ext, 0) + 1
        else:
            extension_counts[""] = extension_counts.get("", 0) + 1

    return extension_counts


def display_recommendations(
    extension_counts: dict[str, int],
    detected_groups: set[str],
) -> None:
    """Display recommendations for optional dependencies using Rich tables.

    Args:
        extension_counts: Dictionary mapping extensions to file counts
        detected_groups: Set of detected dependency groups
    """
    install_method = _detect_install_method()

    # Calculate file counts per group
    group_file_counts: dict[str, int] = {}
    for ext, count in extension_counts.items():
        if ext in EXTENSION_REGISTRY:
            group = EXTENSION_REGISTRY[ext]
            group_file_counts[group] = group_file_counts.get(group, 0) + count

    # Create and configure the table
    table = Table(title="Optional Dependency Recommendations")
    table.add_column("Group", style="cyan", no_wrap=True)
    table.add_column("Files Found", justify="right")
    table.add_column("Install Command", no_wrap=True)
    table.add_column("Prerequisites")

    # Add rows for each detected group, sorted by name for consistency
    for group in sorted(detected_groups):
        file_count = group_file_counts.get(group, 0)

        # Check if group is already installed
        is_installed = is_group_installed(group)

        # Check for broken combos
        broken_msg = _is_broken_combo(group)

        # Get the canonical extra name
        extra_name = _get_extra_name(group)

        # Generate install command
        if broken_msg:
            install_cmd = f"[yellow]⚠ {broken_msg}[/yellow]"
        elif not extra_name:
            # No extra for this group (e.g., parsers are core)
            install_cmd = "[dim](core dependency)[/dim]"
        else:
            if install_method == "pipx":
                # Escape square brackets for Rich markup
                install_cmd = f'pipx inject fo-core "fo-core\\[{extra_name}]" --force'
            else:
                # Escape square brackets for Rich markup; quote for shell safety
                install_cmd = f'pip install "fo-core\\[{extra_name}]"'

        prerequisites = ", ".join(SYSTEM_PREREQUISITES.get(group, ["-"]))

        # Color code based on installation status
        if is_installed:
            # Green for installed
            group_name = f"[green]{group} ✓[/green]"
            status_style = "dim"
        else:
            # Yellow for recommended (not installed)
            group_name = f"[yellow]{group}[/yellow]"
            status_style = ""

        table.add_row(
            group_name,
            f"{file_count}" if not is_installed else f"[dim]{file_count}[/dim]",
            f"[{status_style}]{install_cmd}[/{status_style}]" if status_style else install_cmd,
            f"[{status_style}]{prerequisites}[/{status_style}]" if status_style else prerequisites,
        )

    console.print(table)


def _install_single_group(group: str) -> bool:
    """Install a single optional dependency group via pip or pipx.

    Returns True on success, False on failure.
    """
    install_method = _detect_install_method()
    extra_name = _get_extra_name(group)

    if not extra_name:
        console.print(f"[yellow]⚠ {group} is a core dependency, no installation needed[/yellow]")
        return True

    # Check for broken combos
    broken_msg = _is_broken_combo(group)
    if broken_msg:
        console.print(f"[yellow]⚠ Skipping {group}: {broken_msg}[/yellow]")
        return False

    if install_method == "pipx":
        install_cmd = ["pipx", "inject", "fo-core", f"fo-core[{extra_name}]", "--force"]
    else:
        install_cmd = [sys.executable, "-m", "pip", "install", f"fo-core[{extra_name}]"]

    console.print(f"\n[bold]Installing {group} (extra: {extra_name})...[/bold]")

    try:
        result = subprocess.run(
            install_cmd,
            check=False,
            capture_output=False,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )

        if result.returncode == 0:
            console.print(f"[green]✓ Successfully installed {group}[/green]")
            return True

        console.print(f"[red]✗ Failed to install {group} (exit code {result.returncode})[/red]")
    except FileNotFoundError:
        if install_method == "pipx":
            console.print("[red]✗ Cannot find 'pipx' executable. Is pipx installed?[/red]")
        else:
            console.print("[red]✗ Cannot find 'pip' executable. Is pip installed?[/red]")
    except subprocess.TimeoutExpired:
        console.print(f"[red]✗ Timed out installing {group}[/red]")
    except (subprocess.SubprocessError, OSError) as exc:
        console.print(f"[red]✗ Error installing {group}: {exc}[/red]")

    return False


def install_groups(groups: set[str]) -> None:  # noqa: C901
    """Interactively install optional dependency groups using pip or pipx.

    Prompts the user for confirmation before installing.
    Respects the global dry-run flag (via ``_get_state().dry_run``) to skip actual installation.
    Handles subprocess failures and timeouts gracefully and continues with
    remaining groups.  Each install has a timeout (see ``_INSTALL_TIMEOUT_SECONDS``).

    Args:
        groups: Set of dependency group names to install
    """
    if not groups:
        console.print("[yellow]No groups to install.[/yellow]")
        return

    install_method = _detect_install_method()

    # Display groups to be installed
    groups_list = sorted(groups)
    console.print(f"\n[bold]Recommended groups to install:[/bold] {', '.join(groups_list)}")

    # Show system prerequisites if any
    has_prerequisites = False
    for group in groups_list:
        if group in SYSTEM_PREREQUISITES:
            if not has_prerequisites:
                console.print("\n[bold yellow]System Prerequisites:[/bold yellow]")
                has_prerequisites = True
            prereqs = ", ".join(SYSTEM_PREREQUISITES[group])
            console.print(f"  • {group}: {prereqs}")

    if has_prerequisites:
        console.print()

    # Ask for confirmation
    if not confirm_action(
        f"Install {len(groups_list)} optional dependency group(s)?",
        default=False,
    ):
        console.print("[yellow]Installation cancelled.[/yellow]")
        return

    # Dry-run mode: skip actual installation
    if _get_state().dry_run:
        console.print("[yellow]Dry-run mode: skipping actual installation.[/yellow]")
        for group in groups_list:
            extra_name = _get_extra_name(group) or group
            if install_method == "pipx":
                console.print(
                    f'  [dim]Would run: pipx inject fo-core "fo-core\\[{extra_name}]" --force[/dim]'
                )
            else:
                console.print(f"  [dim]Would install: fo-core\\[{extra_name}][/dim]")
        return

    # Install each group
    failed_groups: list[str] = []
    for group in groups_list:
        if not _install_single_group(group):
            failed_groups.append(group)

    # Summary
    console.print()
    if failed_groups:
        console.print(
            f"[yellow]Installation completed with errors. "
            f"Failed groups: {', '.join(failed_groups)}[/yellow]"
        )
        console.print("\n[dim]You can retry failed installations manually:[/dim]")
        for group in failed_groups:
            extra_name = _get_extra_name(group) or group
            if install_method == "pipx":
                console.print(f'  [dim]pipx inject fo-core "fo-core\\[{extra_name}]" --force[/dim]')
            else:
                console.print(f'  [dim]pip install "fo-core\\[{extra_name}]"[/dim]')

        # Add uv cache clean hint for pipx
        if install_method == "pipx":
            console.print('\n[dim]If install fails with "no version found", run:[/dim]')
            console.print("  [dim]uv cache clean[/dim]")
            console.print("  [dim]then retry.[/dim]")
    else:
        console.print(f"[green]✓ All {len(groups_list)} group(s) installed successfully![/green]")


def doctor(  # noqa: C901
    path: Path | None = typer.Argument(
        None,
        help="Directory to scan for file types (defaults to current directory).",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    install: bool = typer.Option(
        False,
        "--install",
        help="Automatically install recommended dependency groups.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON.",
    ),
) -> None:
    """Scan directory for file types and recommend optional dependencies.

    Detects file types in the specified directory and recommends which optional
    dependency groups should be installed to support those file types.
    """
    # Respect global --json flag if command-level --json not given
    if not json_output and _get_state().json_output:
        json_output = True

    resolved_path: Path = (path or Path.cwd()).resolve()
    if not resolved_path.is_dir():
        console.print(f"[red]Error: Not a directory: {resolved_path}[/red]")
        raise typer.Exit(code=1)

    # Scan the directory
    extension_counts = scan_directory(resolved_path)

    if not extension_counts:
        if json_output:
            result: dict[str, Any] = {
                "directory": str(resolved_path),
                "files_found": 0,
                "extensions": {},
                "detected_groups": [],
                "missing_groups": [],
            }
            typer.echo(json.dumps(result, indent=2))
        else:
            console.print("[yellow]No files found in directory.[/yellow]")
        raise typer.Exit(code=0)

    # Get the set of extensions
    extensions = set(extension_counts.keys())

    # Determine which groups are needed
    detected_groups = get_groups_for_extensions(extensions)

    if not detected_groups:
        if json_output:
            result = {
                "directory": str(resolved_path),
                "files_found": sum(extension_counts.values()),
                "extensions": extension_counts,
                "detected_groups": [],
                "missing_groups": [],
            }
            typer.echo(json.dumps(result, indent=2))
        else:
            console.print(
                "\n[green]No optional dependencies needed for detected file types.[/green]"
            )
        raise typer.Exit(code=0)

    # Calculate file counts per group
    group_file_counts: dict[str, int] = {}
    for ext, count in extension_counts.items():
        if ext in EXTENSION_REGISTRY:
            group = EXTENSION_REGISTRY[ext]
            group_file_counts[group] = group_file_counts.get(group, 0) + count

    # Check which groups are missing
    missing_groups = get_missing_groups(detected_groups)

    # Build group information
    install_method = _detect_install_method()
    groups_info: list[dict[str, Any]] = []
    for group in sorted(detected_groups):
        file_count = group_file_counts.get(group, 0)
        is_installed = is_group_installed(group)
        prerequisites = SYSTEM_PREREQUISITES.get(group, [])
        extra_name = _get_extra_name(group)

        broken_msg = _is_broken_combo(group)
        if broken_msg:
            install_command = f"(broken: {broken_msg})"
        elif install_method == "pipx" and extra_name:
            install_command = f'pipx inject fo-core "fo-core[{extra_name}]" --force'
        elif extra_name:
            install_command = f"pip install fo-core[{extra_name}]"
        else:
            install_command = "(core dependency)"

        groups_info.append(
            {
                "group": group,
                "files_found": file_count,
                "installed": is_installed,
                "install_command": install_command,
                "prerequisites": prerequisites,
            }
        )

    if json_output:
        result = {
            "directory": str(resolved_path),
            "files_found": sum(extension_counts.values()),
            "extensions": extension_counts,
            "detected_groups": groups_info,
            "missing_groups": sorted(missing_groups),
        }
        typer.echo(json.dumps(result, indent=2))
        raise typer.Exit(code=0)

    # Display recommendations (non-JSON mode)
    console.print(f"\n[bold]Scanning directory:[/bold] {resolved_path}")
    console.print()
    display_recommendations(extension_counts, detected_groups)

    if not missing_groups:
        console.print("\n[green]✓ All recommended dependency groups are already installed![/green]")
        raise typer.Exit(code=0)

    # Install if requested
    if install:
        install_groups(missing_groups)
    else:
        # Show summary of what's missing with actionable install commands
        # Separate groups by whether they can be installed or are broken/core
        installable = []
        warnings_shown = []

        for group in sorted(missing_groups):
            broken_msg = _is_broken_combo(group)
            if broken_msg:
                warnings_shown.append((group, broken_msg))
            elif _get_extra_name(group):
                installable.append(group)

        # Convert installable groups to unique extras
        extras = _groups_to_extras(set(installable))

        if installable:
            # Group names with their corresponding extra
            group_to_extra_display: dict[str, list[str]] = {}
            for group in installable:
                extra = _get_extra_name(group)
                if extra:
                    if extra not in group_to_extra_display:
                        group_to_extra_display[extra] = []
                    group_to_extra_display[extra].append(group)

            console.print(
                f"\n[yellow]Found {len(installable)} missing dependency group(s).[/yellow]"
            )
            console.print("\nInstall them now:")
            for extra in extras:
                groups_for_extra = group_to_extra_display.get(extra, [])
                group_names = " + ".join(groups_for_extra) if groups_for_extra else extra
                if install_method == "pipx":
                    console.print(
                        f'  [cyan]pipx inject fo-core "fo-core\\[{extra}]" --force[/cyan]        # {group_names}'
                    )
                else:
                    console.print(
                        f'  [cyan]pip install "fo-core\\[{extra}]"[/cyan]        # {group_names}'
                    )

            if len(extras) > 1:
                all_extras = ",".join(extras)
                console.print("\nOr install all at once:")
                if install_method == "pipx":
                    console.print(
                        f'  [cyan]pipx inject fo-core "fo-core\\[{all_extras}]" --force[/cyan]'
                    )
                else:
                    console.print(f'  [cyan]pip install "fo-core\\[{all_extras}]"[/cyan]')

            # Add uv cache clean hint for pipx
            if install_method == "pipx":
                console.print('\nIf install fails with "no version found", run:')
                console.print("  [cyan]uv cache clean[/cyan]")
                console.print("then retry.")

        # Show warnings for broken combos
        if warnings_shown:
            console.print()
            for group, msg in warnings_shown:
                console.print(f"[yellow]⚠️  {group} skipped — {msg}[/yellow]")
