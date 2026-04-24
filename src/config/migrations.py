"""Config-schema migration registry.

F6 (hardening roadmap #159): explicit version-bump handling for
serialized ``AppConfig`` dicts. When :data:`config.schema.CURRENT_SCHEMA_VERSION`
is bumped, register a migration here that transforms the pre-bump
dict shape into the post-bump shape. ``ConfigManager.load`` walks
the registry at read time so old ``config.yaml`` files upgrade
automatically on next launch.

Contract
--------
- Each entry maps a *source* version string to a callable
  ``(data: dict) -> dict`` that returns the post-migration data.
- Migrations chain: if the on-disk version is ``"0.5"`` and current
  is ``"1.5"``, and there are migrations for ``"0.5"`` and ``"1.0"``,
  both run in sequence. The migration for version *X* produces the
  data shape at version *X+1*.
- Migrations must be idempotent — running twice on already-migrated
  data should be a no-op. This simplifies recovery after a partial
  load failure.
- Migrations never mutate their input in place; they may, but must
  return the result to signal intent.

Backwards/future-compat
-----------------------
- A config with a version *newer* than ``CURRENT_SCHEMA_VERSION``
  cannot be migrated (we don't have the newer migrations). The
  load path logs a loud WARNING and proceeds best-effort — refusing
  would strand users mid-upgrade.
- A config with a version that has no registered migration AND is
  not the current version is also treated as best-effort with a
  WARNING.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

MigrationFn = Callable[[dict], dict]

# Public registry: sorted by source version string. Populated by
# bumps to ``CURRENT_SCHEMA_VERSION`` as new breaking changes ship.
# Empty today — the schema is at 1.0 with no historical predecessors
# to migrate from. Tests monkeypatch this dict to cover the
# migration-runs and migration-fails paths.
MIGRATIONS: dict[str, MigrationFn] = {}


def migrate_to_current(data: dict, *, from_version: str, to_version: str) -> dict:
    """Walk the migration registry from *from_version* to *to_version*.

    Returns the migrated data dict. Missing migrations are logged
    at WARNING and the data is returned unchanged (best-effort).

    Args:
        data: The serialized config dict as read from YAML.
        from_version: The version stamped in the on-disk config.
        to_version: The version this binary expects
            (typically :data:`config.schema.CURRENT_SCHEMA_VERSION`).

    Returns:
        The (possibly transformed) config dict ready for
        ``AppConfig`` construction.
    """
    if from_version == to_version:
        return data

    current = from_version
    # Sort keys so migration chains run in a deterministic order.
    # String compare is correct for simple ``"0.5"`` → ``"1.0"`` →
    # ``"1.5"`` sequences. Bumps that need non-lexicographic ordering
    # should switch to ``packaging.version.Version`` comparison.
    for step_from in sorted(MIGRATIONS.keys()):
        if step_from < current:
            # Already past this migration's source version — skip.
            continue
        if step_from != current:
            # Gap in the registry — we don't have a migration that
            # starts from ``current``. Warn and bail best-effort.
            logger.warning(
                "No migration registered for config version %s; "
                "loading as-is. If fields were renamed or added in a "
                "newer schema, they may fall back to defaults.",
                current,
            )
            return data
        migration = MIGRATIONS[step_from]
        try:
            data = migration(data)
        except Exception:
            # Re-raise — the caller (ConfigManager.load) decides
            # whether to log + fall back to defaults. We don't want
            # to swallow here because silent migration failure would
            # produce subtly-wrong config at runtime.
            logger.error(
                "Config migration from version %s failed; the caller will fall back to defaults.",
                step_from,
                exc_info=True,
            )
            raise
        current = _next_version(step_from)
        if current == to_version:
            return data

    # Exhausted the registry without reaching to_version — either a
    # future config we can't migrate down, or a gap in the chain.
    if current != to_version:
        logger.warning(
            "Config migration did not reach target version %s (stopped at %s). Loading as-is.",
            to_version,
            current,
        )
    return data


def _next_version(v: str) -> str:
    """Return the version string that follows *v* in the migration chain.

    Trivial implementation: the registry is keyed by source version,
    and the "next" version is simply the next lexicographic key. A
    migration from ``"0.5"`` produces data at the next registered
    version. For a linear ``0.5 → 1.0 → 1.5`` chain this is
    correct; complex branching schemas would need a more formal
    version graph.
    """
    sorted_keys = sorted(MIGRATIONS.keys())
    try:
        idx = sorted_keys.index(v)
    except ValueError:
        return v
    if idx + 1 < len(sorted_keys):
        return sorted_keys[idx + 1]
    # No further migration registered — caller assumes we've reached
    # the current version.
    from config.schema import CURRENT_SCHEMA_VERSION

    return CURRENT_SCHEMA_VERSION
