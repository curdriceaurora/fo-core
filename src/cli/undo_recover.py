"""F7.1 step 8 / §8.2: ``fo recover`` — preview pending durable_move recovery.

The command reads the durable_move journal under ``LOCK_SH`` (per §6.5),
calls the pure :func:`plan_recovery_actions` planner (§8.1), and renders
the planned recovery actions as a table for operator visibility.

Critical: this command is read-only. It calls only the planner — never
the executor. The next CLI start (which runs ``sweep``) is what actually
performs the planned actions.

Exit-code contract:

- ``0`` if the plan contains no retained / actionable entries (i.e. all
  verbs are ``drop``).
- ``1`` if any non-``drop`` action would be taken so scripts can detect
  "needs cleanup".

The §5.1 STARTED disambiguation tier (pre-replace / post-replace /
v1-ambiguous) appears in the rendered reason for v2 ``move started``
rows so operators can correlate sweep's planned action with on-disk
state without re-reading the journal.
"""

from __future__ import annotations

import logging
from pathlib import Path

from undo import _journal
from undo.durable_move import (
    _PlannedAction,
    plan_recovery_actions,
    read_journal_under_shared_lock,
)

logger = logging.getLogger(__name__)

# Verbs that represent actionable work (anything sweep would do beyond
# trivially dropping a finished entry). ``drop`` alone is finished
# bookkeeping — no recovery needed.
_ACTIONABLE_VERBS = frozenset({"retain", "unlink_src_then_drop", "drop_tmp_then_drop"})


def recover_command(
    journal: Path | None = None,
    verbose: bool = False,
) -> int:
    """Entry point for ``fo recover``.

    Args:
        journal: Override the default ``durable_move.journal`` path.
            ``None`` (default) → :func:`undo._journal.default_journal_path`.
        verbose: Enable DEBUG-level logging for troubleshooting.

    Returns:
        Exit code per §8.2: ``0`` if no actionable entries, ``1`` if any.
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    journal_path = journal if journal is not None else _journal.default_journal_path()

    entries = read_journal_under_shared_lock(journal_path)
    if not entries:
        print(f"no retained entries in {journal_path}")
        return 0

    plan = plan_recovery_actions(entries)
    actionable = [a for a in plan if a.verb in _ACTIONABLE_VERBS]

    if not actionable:
        print(f"no retained entries in {journal_path} ({len(plan)} dropped — already finished)")
        return 0

    _render_plan_table(journal_path, plan, actionable)
    return 1


def _render_plan_table(
    journal_path: Path,
    plan: list[_PlannedAction],
    actionable: list[_PlannedAction],
) -> None:
    """Print a human-readable table of the planned actions.

    Renders only ``actionable`` rows (so finished ``drop`` entries
    don't add noise) but reports the full plan size in the header
    so operators can see how much was already settled.
    """
    print(f"recovery plan for {journal_path}")
    print(
        f"  {len(actionable)} actionable entr{'y' if len(actionable) == 1 else 'ies'} "
        f"(of {len(plan)} total)"
    )
    print()
    # Column header — fixed widths for readability without pulling in
    # rich (which would inflate startup time for what's a debugging
    # one-shot command).
    header = f"{'OP':<10} {'STATE':<10} {'VERB':<22} REASON"
    print(header)
    print("-" * len(header))
    for action in actionable:
        e = action.entry
        op = e.op
        state = e.state
        verb = action.verb
        reason = _decorate_reason(action)
        print(f"{op:<10} {state:<10} {verb:<22} {reason}")
        print(f"           src: {e.src}")
        print(f"           dst: {e.dst}")
        if e.tmp_path is not None:
            print(f"           tmp: {e.tmp_path}")


def _decorate_reason(action: _PlannedAction) -> str:
    """Annotate the planner's reason with the §5.1 disambiguation tier
    for v2 ``move started`` rows so operators see WHY sweep would take
    the chosen verb (pre-replace tmp orphan vs. post-replace src-only
    cleanup).
    """
    e = action.entry
    if e.op != "move" or e.state != "started":
        return action.reason
    if e.schema == 2 and e.tmp_path is not None:
        if action.verb == "drop_tmp_then_drop":
            tier = "pre-replace"
        elif action.verb == "unlink_src_then_drop":
            tier = "post-replace"
        else:
            return action.reason
        return f"[{tier}] {action.reason}"
    return f"[v1-ambiguous] {action.reason}"
