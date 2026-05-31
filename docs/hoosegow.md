# Hoosegow Migration Plan

## Goal

Rename the project to Hoosegow everywhere: code, tests, scripts, documentation,
browser UI, runtime labels, filesystem defaults, environment variables, sandbox
base metadata, and repository paths.

Completion gate:

- `pytest -q` passes.
- A case-insensitive repository search for the legacy project spelling returns
  zero matches in tracked and generated-in-repo files.
- File and directory names no longer contain the legacy project spelling.

Current inventory from a case-insensitive search:

- 803 text matches across 42 files.
- 10 repository paths contain the legacy project spelling.
- Major affected surfaces: CLI entrypoint, guest PTY controller, helper scripts,
  frontend copy and storage keys, CSS class names, Flask app config keys,
  auth/state environment variables, Microsandbox base names, tests, README, and
  release/security/spec docs.

## Target Naming

- Product display name: `Hoosegow`
- CLI module and script: `hoosegow.py`
- In-sandbox PTY controller: `guest/hoosegow-ptyd.py`
- Microsandbox helper script: `scripts/hoosegow-msb`
- State home default: `~/.hoosegow`
- Environment variable prefix: `HOOSEGOW_`
- Prepared base default: `hoosegow-microsandbox-local`
- Internal app config prefix: `hoosegow_`
- Validation helper module: `server/hoosegow_validation.py`
- CSS/localStorage prefix: `hoosegow`
- Repository URL/docs examples: `github.com/billroy/hoosegow`

## Staged Technical Implementation

### Stage 1: Mechanical Repository Rename

- Move root entrypoint from the old module name to `hoosegow.py`.
- Move `guest/*-ptyd.py` to `guest/hoosegow-ptyd.py`.
- Move `scripts/*-msb` to `scripts/hoosegow-msb`.
- Move `server/*_validation.py` to `server/hoosegow_validation.py`.
- Rename test files whose filenames include the legacy spelling.
- Update all imports, subprocess commands, script paths, and smoke-test paths to
  the new filenames.
- Run `python3 -m py_compile hoosegow.py server/*.py guest/hoosegow-ptyd.py scripts/*.py`.

### Stage 2: Public CLI and Runtime Defaults

- Change argparse `prog`, version output, banner text, startup logs, and help
  text to Hoosegow.
- Change default state home to `~/.hoosegow`.
- Replace every old environment variable with the new `HOOSEGOW_` prefix.
- Update bootstrap credential handling, production mode, allowed origins,
  session duration, workspace roots, port pool, terminal limits, sandbox limits,
  and real Microsandbox smoke toggles to the new prefix.
- Do not keep compatibility aliases for old environment variables, CLI names,
  state paths, app config keys, base names, or controller paths. Backward
  compatibility is explicitly out of scope for this rename.

### Stage 3: Server Internals and Sandbox Runtime

- Rename runtime classes and errors to `HoosegowRuntimeError` and
  `HoosegowSandboxSpec`.
- Rename local aliases such as base-service imports, app config keys, terminal
  state keys, sandbox-service keys, request-id headers, and labels.
- Change Microsandbox labels from the old app name to `hoosegow`.
- Change base preparation sandbox names, metadata paths, validation sandbox
  names, and default snapshot names to `hoosegow-microsandbox-local`.
- Change guest runtime directories and logs from old-name paths to Hoosegow
  paths, including `/var/lib/hoosegow`, `/opt/hoosegow-venv`, base version files,
  controller logs, fd limit filenames, and sysctl filenames.
- Update the PTY controller health checks, server version string, and
  user-visible errors.

### Stage 4: Frontend Surface

- Change HTML titles, login page heading, top-bar title, GitHub menu label, and
  empty/setup state copy to Hoosegow.
- Change GitHub link to the Hoosegow repository URL.
- Rename CSS class prefixes from the old shell prefix to `hoosegow-shell`.
- Rename localStorage keys for theme, sidebar width, and sidebar collapsed state.
- Update frontend tests that assert UI copy, CSS selectors, localStorage keys,
  and links.
- Manually verify the app shell and login screen after tests pass.

### Stage 5: Tests and Test Fixtures

- Rename test modules and test function names that include the legacy spelling.
- Update import targets from the old module to `hoosegow`.
- Update fixtures to set `HOOSEGOW_HOME` and related variables.
- Update expected strings for version output, error messages, request headers,
  logs, base snapshots, smoke outputs, localStorage keys, and CSS selectors.
- Keep the product-surface tests strict by adding a final test that scans the
  repository tree for the old spelling and fails on any match.
- Run the focused suites first, then the full suite.

### Stage 6: Documentation and Release Material

- Rewrite `README.md`, `docs/security.md`, `docs/spec.md`,
  `docs/release-checklist.md`, `docs/passthrough.md`,
  `docs/pty-controller-spike.md`, `docs/release-smokes.md`, and excavation notes
  for Hoosegow.
- Replace install, clone, launch, auth, state, base-preparation, and smoke-test
  commands with Hoosegow commands.
- Keep useful doc-only history only if the completion gate is reconsidered.
  Current impact: README/docs contain 339 case-insensitive matches across 9
  files. Passing the zero-match gate requires rewriting or removing those
  references from the repository.

### Stage 7: State and Operator Cutover

- Treat this as a hard cutover. Do not migrate existing local state into
  `~/.hoosegow`.
- Do not migrate browser localStorage preferences; theme and sidebar preferences
  reset under the new keys.
- Document Microsandbox cleanup for old prepared bases. Running sandboxes from
  the old server are unsupported after the rename.
- Rebuild the Hoosegow prepared base after code paths and guest files are
  renamed.

### Stage 8: Verification

- Run `pytest -q`.
- Run the smoke compile command from Stage 1.
- Run focused product/auth/sandbox tests if the full suite fails, then fix and
  rerun the full suite.
- Run a repository path scan for the legacy spelling.
- Run a repository content scan for the legacy spelling.
- Confirm no filename, doc text, test expectation, generated asset, cache file,
  or vendored project file in the repo contains the legacy spelling.
- For real Microsandbox validation, run the marked real smoke suite with the new
  `HOOSEGOW_` environment variables after rebuilding the prepared base.

## Decisions

- **No backward compatibility.** Old CLI names, environment variables, config
  keys, state directories, sandbox base names, controller paths, and browser
  storage keys should not keep aliases.
- **Hard state cutover.** Existing local state is not migrated. Hoosegow starts
  with `~/.hoosegow`.
- **No browser preference migration.** Renamed localStorage keys reset browser
  preferences.
- **Running old sandboxes are unsupported.** Users should stop or recreate
  running sandboxes after the rename.

## Issues To Resolve

- **Prepared base lifecycle.** Hoosegow needs a newly built and validated
  prepared base. Docs should tell operators how to remove old snapshots if they
  want cleanup.
- **Doc-only history exception.** README/docs currently contain 339
  case-insensitive legacy-name matches across 9 files. Keeping that history
  conflicts with the zero-match completion gate; decide whether to rewrite it
  now or formally relax the gate for documentation.
- **Test guard design.** A repository-scan test must ignore `.git` and external
  caches, and it should avoid storing the forbidden spelling in the test source.
- **Case sensitivity.** The completion gate should be interpreted as
  case-insensitive so title-case, uppercase env vars, lowercase filenames, and
  mixed-case copy are all removed unless the doc-only exception is approved.
- **External references.** GitHub repository URLs, package names, screenshots,
  release notes, and any future distribution metadata must be renamed outside
  the codebase too.
