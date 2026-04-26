# File Organizer CLI Reference

All commands are available via `fo` or the short alias `fo`.

## Global Options

These options apply to every command and may be passed before or after the command name:

| Flag | Short | Description |
|------|-------|-------------|
| `--verbose` | `-v` | Enable verbose output |
| `--dry-run` | | Preview changes without executing |
| `--json` | | Output results as JSON |
| `--yes` | `-y` | Auto-confirm all prompts |
| `--no-interactive` | | Disable interactive prompts |
| `--help` | | Show help and exit |

---

## Top-Level Commands

### `version`

Show the application version.

```bash
fo version
```

---

### `organize`

Organize files in a directory using AI models.

**Usage:**

```bash
fo organize INPUT_DIR OUTPUT_DIR [OPTIONS]
```

**Arguments:**

- `INPUT_DIR` — Directory containing files to organize
- `OUTPUT_DIR` — Destination directory for organized files

**Options:**

- `--dry-run` — Preview without moving files
- `--verbose, -v` — Verbose output
- `--max-workers INTEGER` — Cap parallel worker count
- `--sequential` — Force single-worker sequential processing
- `--no-vision`, `--text-only` — Disable vision model loading and use extension fallback for images
- `--prefetch-depth INTEGER` — Parallel task queue-ahead depth (`0` disables prefetch queueing)
- `--no-prefetch` — Backward-compatible alias for `--prefetch-depth 0`

**Examples:**

```bash
# Organize ~/Downloads into ~/Organized
fo organize ~/Downloads ~/Organized

# Preview what would happen (no files moved)
fo organize ~/Downloads ~/Organized --dry-run

# Verbose output
fo organize ~/Downloads ~/Organized --verbose

# Limit CPU/IO pressure on constrained machines
fo organize ~/Downloads ~/Organized --max-workers 2 --prefetch-depth 1

# Strict sequential mode for deterministic debugging
fo organize ~/Downloads ~/Organized --sequential

# Disable AI vision processing and use extension-based image fallback
fo organize ~/Downloads ~/Organized --no-vision

# Backward-compatible alias
fo organize ~/Downloads ~/Organized --no-prefetch
```

> **Note:** To set a default methodology (PARA, Johnny Decimal, etc.) or override AI models, use `fo config edit` before running organize.

---

### `preview`

Preview how files would be organized without moving them (dry-run shortcut).

**Usage:**

```bash
fo preview INPUT_DIR
```

**Examples:**

```bash
fo preview ~/Downloads
fo preview ~/Downloads
```

---

### `search`

Search for files by name pattern with optional type filtering, or use hybrid
BM25+vector semantic search to find files by content relevance.

**Usage:**

```bash
fo search QUERY [DIRECTORY] [OPTIONS]
```

**Arguments:**
- `QUERY` — Search query (glob pattern like `*.pdf`, keyword like `report`, or
  a natural-language phrase when using `--semantic`)
- `DIRECTORY` — Directory to search in (default: current directory)

**Options:**
- `--type, -t TEXT` — Filter by type: `text`, `image`, `video`, `audio`, `archive`
- `--limit, -n INTEGER` — Max results to show (default: 50)
- `--recursive / --no-recursive` — Search subdirectories (default: recursive)
- `--json` — Output as JSON array
- `--semantic` — Use hybrid BM25+vector semantic search instead of filename
  matching; ranks results by content relevance using Reciprocal Rank Fusion

**Examples:**

```bash
# Search by glob pattern
fo search "*.pdf" ~/Documents

# Keyword search (case-insensitive)
fo search "report" ~/Documents

# Filter by type
fo search "*" ~/Pictures --type image

# Non-recursive, limited results
fo search "*.log" /var/log --no-recursive --limit 10

# JSON output for scripting
fo search "*.py" ./src --json

# Semantic search — finds files by content relevance, not just filename
fo search "quarterly budget forecast" ~/Documents --semantic

# Semantic search with type filter and JSON output
fo search "meeting notes" ~/work --semantic --type text --json
```

---

### `analyze`

Analyze a file using AI and show its description, category, and confidence score.

**Usage:**

```bash
fo analyze FILE [OPTIONS]
```

**Arguments:**
- `FILE_PATH` — Path to the file to analyze

**Options:**
- `--verbose, -v` — Show additional details (model name, processing time, content length)
- `--json` — Output as JSON

**Examples:**

```bash
# Basic analysis
fo analyze ~/Documents/report.pdf

# Verbose output
fo analyze ~/Documents/report.pdf --verbose

# JSON output for scripting
fo analyze ~/Documents/report.pdf --json
```

> **Note:** Requires Ollama to be installed and running with a text model available.

---

### `doctor`

Scan a directory for file types and recommend optional dependencies.

**Usage:**

```bash
fo doctor PATH [OPTIONS]
```

**Arguments:**

- `PATH` — Directory to scan for file types

**Options:**

- `--install` — Automatically install recommended dependency groups
- `--json` — Output results as JSON

---

### `setup`

Interactive setup wizard for first-run configuration.

**Usage:**

```bash
fo setup [COMMAND]
```

Running `setup` without a subcommand launches the wizard with default settings.

#### `setup run`

Run the setup wizard to configure File Organizer.

```bash
fo setup run [OPTIONS]
```

**Options:**

- `--mode, -m` — Setup mode: `quick-start` (default) or `power-user`
- `--profile, -p` — Profile name (default: `default`)
- `--dry-run` — Preview configuration without saving

---

### `hardware-info`

Detect hardware capabilities and print model-sizing recommendations.

**Usage:**

```bash
fo hardware-info [OPTIONS]
```

**Options:**
- `--json` — Output the hardware profile as JSON

**Examples:**

```bash
# Human-readable hardware summary
fo hardware-info

# JSON output for automation or debugging
fo hardware-info --json
```

> **Why it exists:** This command exposes the same hardware profile the app uses to choose sane defaults for model size and worker count, which helps explain performance differences across machines.

---

### `undo`

Undo file operations.

**Usage:**

```bash
fo undo [OPTIONS]
```

**Options:**
- `--operation-id INTEGER` — Specific operation ID to undo
- `--transaction-id TEXT` — Transaction ID to undo (undoes all operations in a transaction)
- `--dry-run` — Preview without executing
- `--verbose, -v` — Verbose output

**Examples:**

```bash
# Undo the last operation
fo undo

# Undo a specific operation
fo undo --operation-id 42

# Undo all operations in a transaction
fo undo --transaction-id abc123
```

**Behavior notes:**

- `--dry-run` previews the exact undo action without modifying history.
- When both selectors are provided, `--transaction-id` takes precedence over `--operation-id`.
- Empty or whitespace-only transaction IDs are rejected instead of being treated as valid input.

---

### `redo`

Redo previously undone file operations.

**Usage:**

```bash
fo redo [OPTIONS]
```

**Options:**
- `--operation-id INTEGER` — Specific operation ID to redo
- `--dry-run` — Preview without executing
- `--verbose, -v` — Verbose output

**Behavior notes:**

- `--dry-run` previews the redo action without changing history.
- `operation_id=0` is treated as a valid operation ID rather than falling back to “redo last”.

---

### `history`

View operation history.

**Usage:**

```bash
fo history [OPTIONS]
```

**Options:**
- `--limit INTEGER` — Maximum number of operations to show (default: 10)
- `--type TEXT` — Filter by operation type
- `--status TEXT` — Filter by status
- `--stats` — Show statistics summary
- `--verbose, -v` — Verbose output

**Examples:**

```bash
fo history
fo history --limit 50
fo history --stats
```

---

### `recover`

Preview pending `durable_move` recovery actions without executing them.

Reads the F7.1 durable-move journal under a shared file lock, runs the pure
recovery planner, and prints the planned sweep verbs and reasons. Exits 0 if
nothing actionable; exits 1 if any recovery work would be performed (so shell
scripts can detect a stuck journal without invoking the sweep itself).

**Usage:**

```bash
fo recover [OPTIONS]
```

**Options:**
- `--journal PATH` — Override path to `durable_move.journal` (defaults to the user state dir)
- `--verbose, -v` — Verbose output

**Examples:**

```bash
fo recover
fo recover --journal /tmp/journal.bin --verbose
```

---

### `analytics`

Display storage analytics dashboard.

**Usage:**

```bash
fo analytics [DIRECTORY] [OPTIONS]
```

**Arguments:**
- `DIRECTORY` — Directory to analyze (optional; defaults to configured workspace)

**Options:**
- `--verbose, -v` — Verbose output

**Examples:**

```bash
fo analytics
fo analytics ~/Documents
```

---

## Sub-Commands

### `benchmark` — Performance Benchmarking

Measure file processing performance with statistical output, warmup exclusion,
suite selection, and baseline comparison with regression detection.

#### `benchmark run`

Run a performance benchmark on a directory of files.

**Usage:**

```bash
fo benchmark run [INPUT_PATH] [OPTIONS]
```

**Arguments:**

- `INPUT_PATH` — Path to files to benchmark (default: `tests/fixtures/`)

**Options:**

- `--iterations INTEGER, -i INTEGER` — Number of measured iterations (default: `10`, min: `1`)
- `--warmup INTEGER, -w INTEGER` — Warmup iterations excluded from statistics (default: `3`, min: `0`)
- `--suite TEXT, -s TEXT` — Benchmark suite to run: `io`, `text`, `vision`, `audio`, `pipeline`, `e2e` (default: `io`)
  - `io`: file stat/read overhead baseline
  - `text`: `TextProcessor.process_file()` path with deterministic benchmark model stubs
  - `vision`: `VisionProcessor.process_file()` path with deterministic benchmark model stubs
  - `audio`: audio metadata extraction + rule-based classification path (uses synthetic metadata only when optional extractor dependencies are unavailable)
  - `pipeline`: `PipelineOrchestrator.process_batch()` staged path
  - `e2e`: full `FileOrganizer.organize()` pass with real writes in an isolated temp workspace
- `--json` — Output results as JSON instead of a Rich table
- `--compare PATH` — Path to baseline JSON file for regression comparison

**Output Metrics (JSON schema):**

- `suite` — Suite name that was run
- `effective_suite` — Effective suite semantics used for execution (for example, `audio` may degrade to `io` semantics when no audio candidates are available)
- `degraded` — `true` when the run used degraded semantics (skip/fallback), otherwise `false`
- `degradation_reasons` — Stable machine-readable degradation reason codes; empty when `degraded` is `false`
- `runner_profile_version` — Benchmark runner semantics profile version for baseline compatibility checks
- `files_count` — Number of files actually processed by the selected suite semantics
- `hardware_profile` — Hardware detection info (CPU, memory, GPU)
- `results.median_ms` — Median iteration time in milliseconds
- `results.p95_ms` — 95th percentile iteration time
- `results.p99_ms` — 99th percentile iteration time
- `results.stddev_ms` — Standard deviation of iteration times
- `results.throughput_fps` — Throughput in files per second (based on median)
- `results.iterations` — Number of measured iterations

When `--compare` is used, JSON also includes:

- `comparison.deltas_pct.*` — Percentage delta versus the baseline for each metric
- `comparison.regression` — `true` if current p95 crossed the regression threshold
- `comparison.threshold` — Threshold multiplier used for regression detection — fixed at `1.2` for the CLI (not user-configurable; emitted in the JSON for consumer reference)
- `comparison_profile_warning` — Present when comparing against a baseline built with a different `runner_profile_version`

**Regression Detection:**

When `--compare` is provided, compares current results against a baseline JSON
file. Flags a regression if p95 exceeds 120% of the baseline p95.

**Examples:**

```bash
# Benchmark files in Downloads with default settings
fo benchmark run ~/Downloads

# Run with 5 iterations, no warmup, JSON output
fo benchmark run ~/Documents --iterations 5 --warmup 0 --json

# Run text suite and compare against baseline
fo benchmark run tests/fixtures/ --suite text --json --compare baseline.json

# Save baseline for future comparison
fo benchmark run tests/fixtures/ --json > baseline.json
```

Audio suite behavior note:
- `audio` intentionally differs from `text`/`vision`: it exercises real metadata extraction + classification and only falls back to synthetic metadata when optional extractor dependencies are unavailable.

---

### `config` — Configuration Management

Manage configuration profiles.

#### `config show`

Display the current configuration profile.

```bash
fo config show [--profile PROFILE]
```

Options:
- `--profile TEXT` — Profile name (default: `default`)

#### `config list`

List all available configuration profiles.

```bash
fo config list
```

#### `config edit`

Edit a configuration profile.

```bash
fo config edit [OPTIONS]
```

Options:
- `--profile TEXT` — Profile name to edit (default: `default`)
- `--text-model TEXT` — Set the text model name
- `--vision-model TEXT` — Set the vision model name
- `--temperature FLOAT` — Set temperature (0.0–1.0)
- `--device TEXT` — Set device (`auto`, `cpu`, `cuda`, `mps`, `metal`)
- `--methodology TEXT` — Set default methodology (`none`, `para`, `jd`)

**Examples:**

```bash
fo config show
fo config show --profile work
fo config edit --text-model qwen2.5:3b-instruct-q4_K_M
fo config edit --device cuda --methodology para
fo config edit --profile work --temperature 0.7
```

---

### `model` — AI Model Management

Manage AI models via Ollama.

#### `model list`

List available models with their install status.

```bash
fo model list [--type TYPE]
```

Options:
- `--type TEXT` — Filter by type: `text`, `vision`, or `audio`

#### `model pull`

Download a model via Ollama.

```bash
fo model pull MODEL_NAME
```

**Arguments:**
- `NAME` — Model name to download (e.g. `qwen2.5:3b-instruct-q4_K_M`)

#### `model cache`

Show model cache statistics.

```bash
fo model cache
```

**Examples:**

```bash
fo model list
fo model list --type vision
fo model pull qwen2.5:3b-instruct-q4_K_M
fo model cache
```

---

### `copilot` — AI Assistant

Interactive AI copilot for file organisation.

#### `copilot chat`

Chat with the file-organisation copilot.

```bash
fo copilot chat [MESSAGE] [--dir DIRECTORY]
```

Arguments:
- `MESSAGE` — Single message (optional; omit to start interactive REPL)

Options:
- `--dir, -d TEXT` — Working directory for file operations

**Examples:**

```bash
# Interactive REPL
fo copilot chat

# Single question
fo copilot chat "Help me organize my photos"

# Scoped to a specific directory
fo copilot chat --dir ~/Documents "What duplicates do I have?"
```

#### `copilot status`

Show the status of the AI copilot engine and available models.

```bash
fo copilot status
```

Displays:
- Number of available Ollama models
- Model names (first 5)
- Copilot readiness status

**Examples:**

```bash
fo copilot status
fo copilot status
```

---

### `daemon` — Background File Watcher

Run the file watcher as a background daemon.

#### `daemon start`

```bash
fo daemon start [OPTIONS]
```

Common options: `--watch-dir PATH`, `--output-dir PATH`

#### `daemon stop`

```bash
fo daemon stop
```

#### `daemon status`

```bash
fo daemon status
```

#### `daemon watch`

Watch a directory for file events and stream them in real-time.

**Usage:** fo daemon watch WATCH_DIR [OPTIONS]

Arguments:
- `WATCH_DIR` — Directory to watch for file events

Options:
- `--poll-interval FLOAT` — Seconds between polls (default: 1.0)

**Examples:**

```bash
fo daemon watch ~/Inbox
fo daemon watch ~/Documents --poll-interval 2.0
```

#### `daemon process`

One-shot: organize files in a directory and display a summary.

```bash
fo daemon process INPUT_DIR OUTPUT_DIR [OPTIONS]
```

Arguments:
- `INPUT_DIR` — Directory containing files to process
- `OUTPUT_DIR` — Destination directory for organized files

Options:
- `--dry-run` — Preview changes without moving files

**Examples:**

```bash
fo daemon process ~/Inbox ~/Organized

# Preview without moving
fo daemon process ~/Downloads ~/Organized --dry-run
```

Displays a summary table with:
- Total files processed
- Number of files organized
- Skipped and failed counts
- Folder structure created

**Examples:**

```bash
fo daemon start --watch-dir ~/Inbox --output-dir ~/Organized
fo daemon status
fo daemon stop
```

---

### `dedupe` — Duplicate File Management

Find and manage duplicate files.

#### `dedupe scan`

Scan a directory for duplicate files.

```bash
fo dedupe scan DIRECTORY [OPTIONS]
```

#### `dedupe report`

Generate a duplication report.

```bash
fo dedupe report DIRECTORY [OPTIONS]
```

**Arguments:**
- `DIRECTORY` — Directory to scan

#### `dedupe resolve`

Interactively or automatically resolve duplicates.

```bash
fo dedupe resolve DIRECTORY [OPTIONS]
```

**Arguments:**
- `DIRECTORY` — Directory to scan for duplicates

**Behavior notes:**

- Automatic strategies prompt for confirmation unless batch mode is enabled.
- Manual selection and confirmation prompts propagate `Ctrl+C` cleanly.
- Dry runs report actual simulated removals and estimated space savings without deleting files.
- Successful-removal summaries reflect what was actually removed rather than the original selection count.

**Examples:**

```bash
fo dedupe scan ~/Images
fo dedupe report
fo dedupe resolve
```

---

### `rules` — Organisation Rules

Manage copilot organisation rules and rule sets.

#### `rules list`

List all rules in a rule set.

```bash
fo rules list [--set RULE_SET]
```

#### `rules sets`

List available rule sets.

```bash
fo rules sets
```

#### `rules add`

Add a new rule to a rule set.

```bash
fo rules add RULE_NAME [OPTIONS]
```

**Arguments:**
- `NAME` — Rule name

**Options:**
- `--ext TEXT` — File extension filter (e.g. `.pdf,.docx`)
- `--pattern TEXT` — Filename glob pattern
- `--action, -a TEXT` — Action type: `move`, `rename`, `tag`, `categorize`, `archive`, `copy`, `delete` (default: `move`)
- `--dest, -d TEXT` — Destination path or pattern
- `--priority, -p INTEGER` — Rule priority (higher = runs first; default: 0)
- `--set, -s TEXT` — Target rule set (default: `default`)

#### `rules remove`

Remove a rule from a rule set.

```bash
fo rules remove RULE_NAME [--set RULE_SET]
```

**Arguments:**
- `NAME` — Rule name to remove

#### `rules toggle`

Enable or disable a rule.

```bash
fo rules toggle RULE_NAME [--set RULE_SET]
```

**Arguments:**
- `NAME` — Rule name to toggle

#### `rules preview`

Preview what rules would do against a directory (dry-run).

```bash
fo rules preview DIRECTORY [OPTIONS]
```

Options:
- `--set, -s TEXT` — Rule set to evaluate (default: `default`)
- `--recursive/--no-recursive` — Recurse into subdirectories (default: true)
- `--max-files INTEGER` — Maximum files to scan (default: 500)

#### `rules export`

Export a rule set to YAML.

```bash
fo rules export [--set RULE_SET] [--output FILE]
```

#### `rules import`

Import a rule set from a YAML file.

```bash
fo rules import FILE [--set RULE_SET]
```

**Arguments:**
- `FILE` — YAML file to import

**Examples:**

```bash
# List rules in the default rule set
fo rules list

# Add a rule to move PDFs to a Docs folder
fo rules add move-pdfs --ext .pdf --action move --dest Docs

# Add a rule with glob pattern, high priority
fo rules add archive-old --pattern "*.2022*" --action archive --priority 10

# Preview rules against a directory
fo rules preview ~/Downloads

# Export/import rule sets
fo rules export --set work --output work-rules.yaml
fo rules import work-rules.yaml
```

---

### `suggest` — Smart File Suggestions

Generate AI-powered file organisation suggestions using pattern analysis.

#### `suggest files`

Generate organisation suggestions for files in a directory.

```bash
fo suggest files DIRECTORY [OPTIONS]
```

Options:
- `--min-confidence FLOAT` — Minimum confidence threshold 0–100 (default: 40.0)
- `--max-results INTEGER` — Maximum suggestions (default: 50)
- `--json` — Output as JSON
- `--dry-run` — Preview mode

#### `suggest apply`

Apply accepted suggestions.

```bash
fo suggest apply DIRECTORY [OPTIONS]
```

**Arguments:**
- `DIRECTORY` — Directory to organize

#### `suggest patterns`

Analyze naming patterns in a directory.

```bash
fo suggest patterns DIRECTORY [OPTIONS]
```

**Examples:**

```bash
fo suggest files ~/Downloads
fo suggest files ~/Documents --min-confidence 60
fo suggest patterns ~/Projects
```

---

### `marketplace` — Plugin Marketplace

Browse and manage plugins from the marketplace.

#### `marketplace list`

List available plugins.

```bash
fo marketplace list [OPTIONS]
```

Options:
- `--page, -p INTEGER` — Page number (default: 1)
- `--per-page INTEGER` — Results per page (default: 20)
- `--category, -c TEXT` — Filter by category
- `--tag, -t TEXT` — Filter by tag (repeatable)

#### `marketplace search`

Search the marketplace.

```bash
fo marketplace search QUERY [OPTIONS]
```

#### `marketplace info`

Show details for a specific plugin.

```bash
fo marketplace info PLUGIN_NAME
```

**Arguments:**
- `NAME` — Plugin name

#### `marketplace install`

Install a plugin.

```bash
fo marketplace install PLUGIN_NAME [--version VERSION]
```

**Arguments:**
- `NAME` — Plugin to install

#### `marketplace uninstall`

Remove an installed plugin.

```bash
fo marketplace uninstall PLUGIN_NAME
```

**Arguments:**
- `NAME` — Plugin to uninstall

#### `marketplace review`

Add or update a review for a plugin.

```bash
fo marketplace review PLUGIN_NAME [OPTIONS]
```

Arguments:
- `PLUGIN_NAME` — Name of the plugin to review

Options:
- `--user TEXT` — Reviewer ID (required)
- `--rating INTEGER` — Rating from 1 to 5 (required)
- `--title TEXT` — Review title (required)
- `--content TEXT` — Review text (required)

**Examples:**

```bash
fo marketplace review awesome-plugin \
  --user john_doe \
  --rating 5 \
  --title "Great plugin!" \
  --content "This plugin has saved me hours of work!"
```

#### `marketplace installed`

List installed plugins.

```bash
fo marketplace installed
```

#### `marketplace updates`

Check for plugin updates.

```bash
fo marketplace updates
```

#### `marketplace update`

Update a specific plugin.

```bash
fo marketplace update PLUGIN_NAME
```

**Arguments:**
- `NAME` — Plugin to update

---

### `update` — Application Updates

Manage application updates.

#### `update check`

Check for new versions.

```bash
fo update check
```

#### `update install`

Install the latest version.

```bash
fo update install
```

#### `update rollback`

Revert to the previous version.

```bash
fo update rollback
```

---

### `profile` — User Preference Profiles

Manage user preference profiles (powered by the intelligence/learning system).

#### `profile list`

List all available profiles.

```bash
fo profile list
```

#### `profile create`

Create a new profile.

```bash
fo profile create PROFILE_NAME [OPTIONS]
```

#### `profile activate`

Load and activate a profile.

```bash
fo profile activate PROFILE_NAME
```

#### `profile delete`

Delete a profile.

```bash
fo profile delete PROFILE_NAME
```

#### `profile export`

Export a profile to a file.

```bash
fo profile export PROFILE_NAME [--output FILE]
```

#### `profile import`

Import a profile from a file.

```bash
fo profile import FILE [OPTIONS]
```

**Arguments:**
- `FILE` — Profile file to import

#### `profile current`

Show the currently active profile and its statistics.

```bash
fo profile current
```

Displays:
- Active profile name
- Description and version
- Creation and update timestamps
- Statistics (global preferences, directory-specific settings, learned patterns, confidence data)

#### `profile merge`

Merge multiple profiles into one.

```bash
fo profile merge PROFILES... [OPTIONS]
```

Arguments:
- `PROFILES...` — Profile names to merge (requires at least 2)

Options:
- `--output, -o TEXT` — Name for merged profile (required)
- `--strategy, -s TEXT` — Merge strategy for conflicts: `recent`, `frequent`, `confident`, `first`, `last` (default: `confident`)
- `--show-conflicts` — Show conflicts before merging

**Examples:**

```bash
fo profile merge work personal --output merged --strategy confident

# Show conflicts before merging
fo profile merge work personal --output merged --show-conflicts
```

#### `profile migrate`

Migrate a profile to a different version.

```bash
fo profile migrate PROFILE_NAME [OPTIONS]
```

Arguments:
- `PROFILE_NAME` — Name of the profile to migrate

Options:
- `--to-version TEXT` — Target version (required)
- `--no-backup` — Skip backup before migration

**Examples:**

```bash
fo profile migrate work --to-version 2.0

# Migrate without creating backup
fo profile migrate work --to-version 2.0 --no-backup
```

#### `profile validate`

Validate a profile for integrity and compatibility.

```bash
fo profile validate PROFILE_NAME
```

Arguments:
- `PROFILE_NAME` — Name of the profile to validate

**Examples:**

```bash
fo profile validate work
```

---

### `profile template` — Profile Templates

Manage profile templates for common configurations.

#### `profile template list`

List all available templates.

```bash
fo profile template list
```

Displays all available templates with their descriptions.

#### `profile template preview`

Preview a template before applying it.

```bash
fo profile template preview TEMPLATE_NAME
```

Arguments:
- `TEMPLATE_NAME` — Name of the template to preview

Displays:
- Template description
- Preferences summary (naming patterns, folder mappings, category overrides)
- Learned patterns and confidence levels

**Examples:**

```bash
fo profile template preview default
fo profile template preview minimal
```

#### `profile template apply`

Create a profile from a template.

```bash
fo profile template apply TEMPLATE_NAME PROFILE_NAME [OPTIONS]
```

Arguments:
- `TEMPLATE_NAME` — Name of the template to apply
- `PROFILE_NAME` — Name for the new profile

Options:
- `--activate, -a` — Activate the profile immediately after creation

**Examples:**

```bash
fo profile template apply default myprofile

# Apply template and activate it
fo profile template apply minimal myprofile --activate
```

---

**General Profile Examples:**

```bash
fo profile list
fo profile create work --description "Work files config"
fo profile activate work
```

> **Note:** The `profile` command requires the intelligence/learning optional dependencies (`pip install -e ".[all]"`). It degrades gracefully if not installed.

---

### `autotag` — Auto-Tagging

AI-powered tag suggestions and management.

#### `autotag suggest`

Suggest tags for files in a directory.

```bash
fo autotag suggest DIRECTORY [OPTIONS]
```

Options:
- `--top-n, -n INTEGER` — Max suggestions per file (default: 10)
- `--min-confidence FLOAT` — Minimum confidence % (default: 40.0)
- `--json` — Output as JSON

#### `autotag apply`

Apply tags to a file and record for learning.

```bash
fo autotag apply FILE_PATH TAG...
```

**Arguments:**
- `FILE_PATH` — File to tag
- `TAGS` — One or more tags to apply

#### `autotag popular`

Show the most popular tags.

```bash
fo autotag popular [--limit N]
```

Options:
- `--limit, -n INTEGER` — Number of tags to show (default: 20)

#### `autotag recent`

Show recently used tags.

```bash
fo autotag recent [OPTIONS]
```

Options:
- `--days INTEGER` — Days to look back (default: 30)
- `--limit, -n INTEGER` — Number of tags to show (default: 20)

#### `autotag batch`

Batch tag suggestion for a directory.

```bash
fo autotag batch DIRECTORY [OPTIONS]
```

Options:
- `--pattern TEXT` — File pattern (default: `*`)
- `--recursive / --no-recursive` — Recurse into subdirectories (default: true)
- `--json` — Output as JSON

**Examples:**

```bash
fo autotag suggest ~/Documents
fo autotag apply ~/Documents/report.pdf finance quarterly
fo autotag popular --limit 10
fo autotag recent --days 7
fo autotag batch ~/Documents --pattern "*.pdf" --json
```

---

## Short Alias

Use `fo` as a short alias for `fo`:

```bash
fo organize ~/Downloads ~/Organized
fo search "*.pdf" ~/Documents
fo analyze ~/Documents/report.pdf
fo copilot chat
fo dedupe scan ~/Pictures
fo autotag suggest ~/Documents
```

---

## Getting Help

```bash
fo --help
fo COMMAND --help
fo COMMAND SUBCOMMAND --help
```

For example:

```bash
fo rules --help
fo rules add --help
fo suggest --help
```
