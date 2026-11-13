# UNWRITTEN — The Codebase Archaeology Engine

> Don't generate more coding tasks. Generate coding tasks that deserve to be trusted.

UNWRITTEN turns a repository into feature-development tasks and proves each
one is valid before accepting it.

**Status: milestone M1.** This repository currently contains the verification
harness only: the deterministic engine that decides, from executed tests,
whether a task package is valid. Task generation, contract discovery and the
interface are later milestones. The harness makes no model calls.

## The Three-World Test

A task package is a pinned commit, a requirement statement, a reference
implementation, tests and mutants. `unwritten verify` runs it through three
worlds, twice, and accepts it only if every rule holds in both runs.

| World | What is built | The package passes when |
|---|---|---|
| 1 Broken | the pinned commit, untouched | every feature test fails **through an assertion** (not an import, collection or other error); every regression test passes |
| 2 Correct | commit + reference patch | every feature test passes; every regression test ends exactly as in World 1 |
| 3 Deception | commit + reference patch + one mutant, per mutant | every mutant makes at least one test fail that passed in World 2; the killing tests are recorded |
| Repeat | all of the above again | both runs agree on every test outcome, failure class, exception type and mutant result |

World 3 only runs when World 2 passed: mutants are judged against a working
reference or not at all.

Mutants come from two places. **Declared** mutants are patches shipped in the
package. **Generated** mutants are made by the harness with five small
operators (boundary swap, comparison negation, `and`/`or` swap, `not`
removal, forcing a condition true or false), applied only to lines the
reference patch added. The package author cannot choose which generated
mutants run. A survivor rejects the package unless the manifest carries a
waiver with a justification and an approver, and a waiver is written into the
evidence as a manual intervention.

## Quick start

Requirements: Python 3.10+, git, Docker.

    pip install -e ".[dev]"
    python pilot/materialize.py /tmp/unwritten-pilot     # pilot repo at its pinned commit
    unwritten verify packages/pilot-cart-discount-eligibility --repo /tmp/unwritten-pilot

Exit status: `0` accepted, `1` rejected, `2` the harness could not complete
(a harness problem is never reported as a rejection).

    unwritten verify <package> --repo <git repo> [--runner docker|local]
                     [--repeat 2] [--jobs 4] [--out DIR]

`--runner docker` (default) builds the package's `environment/Dockerfile` and
runs each world in a fresh container: no network, read-only root filesystem,
no capabilities, non-root user, memory, CPU and process limits, and a timeout.
`--runner local` runs on the host with **no isolation**; use it only for
repositories you trust. The evidence records which runner was used. There is
no automatic fallback from one to the other.

Behind a TLS-intercepting proxy, image builds may need
`UNWRITTEN_BUILD_NETWORK=host` and `UNWRITTEN_BUILD_CA_BUNDLE=<ca file>`;
see `src/unwritten/runners/docker.py`.

## Shipped packages

| Package | Purpose | Expected verdict |
|---|---|---|
| `packages/pilot-cart-discount-eligibility` | hand-written task on the pilot repo | accepted |
| `packages/invalid-baseline-passes` | same task plus a feature test that already passes at base | rejected: `W1_FEATURE_PASSES_AT_BASE` |
| `packages/invalid-surviving-mutant` | same reference, weak feature tests | rejected: `W3_MUTANT_SURVIVED` |

The pilot repository (`pilot/unwritten-pilot`, a small shopping-cart library)
is stored as plain files. `pilot/materialize.py` turns it into a git
repository with a fixed author and date, so the same files always give the
same commit hash, which the packages pin.

## Package layout (schema 0.1)

    task.json               manifest: task id, repo name and commit, execution settings, mutants, waivers
    statement.md            the requirements a solver is given
    reference.patch         unified diff against the pinned commit
    tests/feature/          must fail at base by assertion, pass with the reference
    tests/regression/       optional; must pass in both worlds
    mutants/*.patch         declared mutants, each a diff on top of the reference
    environment/Dockerfile  interpreter and dependencies; the repo is mounted at run time

## Evidence

Each verification writes a directory:

    evidence.json           verdict, reasons, environment, both runs in full, fingerprints
    logs/run<N>/<world or mutant>/<suite>.log and .json    raw pytest output and per-test records
    mutants/<id>.diff       what each mutant changed

`evidence/` holds the evidence from verifying the three shipped packages on
the Docker runner. The raw logs are left out of version control for size (about
4 MB per package); running the command above recreates them.

Every rejection reason carries a code, the world that raised it and the tests
or mutants responsible.

| Code | Meaning |
|---|---|
| `PACKAGE_INVALID` | the package does not follow the schema |
| `W1_FEATURE_PASSES_AT_BASE` | a feature test passes before the feature exists |
| `W1_FEATURE_FAILS_WITHOUT_ASSERTION` | a feature test fails by error, collection failure, skip or xfail |
| `W1_NO_FEATURE_TESTS`, `W1_NO_REGRESSION_TESTS` | nothing to judge |
| `W1_REGRESSION_FAILS_AT_BASE` | a regression test fails on the untouched commit |
| `REFERENCE_PATCH_DOES_NOT_APPLY`, `REFERENCE_MODIFIES_TESTS` | the reference patch is unusable or edits the repo's tests |
| `W2_FEATURE_FAILS_WITH_REFERENCE` | the reference does not satisfy the feature tests |
| `W2_REGRESSION_BROKEN`, `W2_TEST_SET_DIFFERS` | the reference changes existing behaviour or the collected tests |
| `W3_MUTANT_SURVIVED` | a wrong implementation passes every test |
| `W3_NO_MUTANTS`, `W3_MUTANT_INVALID`, `W3_WAIVER_UNKNOWN_MUTANT` | mutation evidence is missing or unusable |
| `EXECUTION_TIMEOUT` | a suite exceeded the package's time limit |
| `REPEATABILITY_MISMATCH` | two runs on identical inputs disagreed |

## Development

    python -m pytest                  # everything; Docker tests skip, loudly, without a daemon
    python -m pytest -m "not docker"  # local runner only

Source layout: `src/unwritten/` holds `package` (loading), `patches`,
`workspace`, `mutation`, `classify`, `worlds` (one class per world, run in
`PIPELINE` order), `verify` (orchestration and verdict), `runners/` and the two
files that execute inside the sandbox, `runners/_inner/`. The harness has no
runtime dependencies outside the standard library.

## Known limits of M1

- Python repositories tested with pytest only.
- The harness proves that tests detect the feature's absence and detect
  mutants. It does not prove the tests match the written statement, or that
  the task is solvable from the statement alone. That needs a solver that
  sees only the statement (a planned fourth world).
- Generated mutants cover conditions and comparisons on added lines. Wrong
  arithmetic, wrong return values and omitted code paths are covered only by
  declared mutants.
- An equivalent mutant (one that changes nothing observable) needs a human
  waiver; the harness cannot detect equivalence.
- The sandbox trusts the test report written from inside the container. A
  hostile repository could forge it.
- Runs are compared within one machine and one image. Nothing here claims
  identical results across machines.
