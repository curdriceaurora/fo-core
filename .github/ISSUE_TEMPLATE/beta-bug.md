---
name: Beta bug
about: Report a bug in a 2.0.0-beta.X release
title: "[BETA] "
labels: bug, beta
assignees: ""
---

<!--
Thanks for testing the fo-core beta channel!
Filing a bug here helps us decide when this build is ready to graduate to GA.
-->

## What happened

<!-- One-line description of the bug. -->

## fo --debug output

<!--
REQUIRED. Re-run your failing command with --debug and paste the FULL output
below (red error line + traceback). Without this, triage usually has to ask
you to re-run.

Note: the --debug flag is wired in Step 3 of the alpha→beta path. If you
are filing a bug against an alpha release where --debug is not yet available,
substitute `--verbose` and capture stderr.
-->

```text
$ fo --debug <your command>
<paste output here>
```

## fo doctor output

<!-- REQUIRED. Output of `fo doctor`. -->

```text
$ fo doctor
<paste output here>
```

## Environment

- OS:
- Python version (`python --version`):
- fo-core version (`fo version`):
- How installed (`pip install fo-core`, `pip install -e .`, etc.):
- Optional extras installed (`pip show fo-core`):

## Steps to reproduce

1.
2.
3.

## Expected behavior

<!-- What did you think would happen? -->

## Actual behavior

<!-- What happened instead? -->
