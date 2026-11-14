# UNWRITTEN — The Codebase Archaeology Engine

> Don't generate more coding tasks. Generate coding tasks that deserve to be trusted.

UNWRITTEN turns a repository into feature-development tasks and proves each
one is valid before accepting it.

**Status: M1 and M2 accepted; M3a (the deterministic half of the Challenge
Factory) implemented.** This repository contains the verification harness,
contract archaeology, and the checks that hold a task to its own statement:
declared interfaces, criterion traceability, variation measurement and
release states. Task generation, the blind solver and the interface are not
built yet. Nothing here calls a model: verdicts and contract statuses come
from executed tests only. The approved plan for the rest is in
[docs/m3-proposal.md](docs/m3-proposal.md).

A short account of how the pieces fit is in [docs/architecture.md](docs/architecture.md).

## The Three-World Test

A task package is a pinned commit, a requirement statement, a reference
implementation, tests and mutants. `unwritten verify` runs it through three
worlds, twice, and accepts it only if every rule holds in both runs.

| World | What is built | The package passes when |
|---|---|---|
| 1 Broken | the pinned commit, untouched | every feature test fails **through a behavioural assertion**; every regression test and attached contract probe passes |
| 2 Correct | commit + reference patch | every feature test passes; every regression test ends exactly as in World 1; every attached contract still holds |
| 3 Deception | commit + reference patch + one mutant, per mutant | every mutant makes at least one test fail that passed in World 2; the killing tests are recorded |
| Repeat | all of the above again | both runs agree on every test outcome, failure class, exception type and mutant result |

### Failure classes

World 1 counts only the first class as proof that a feature is absent.

| Class | Determined by |
|---|---|
| assertion | an `AssertionError` raised by test code, or pytest's explicit failure (`DID NOT RAISE`, `pytest.fail`) |
| runtime error | any other exception in a test or fixture, including an `AssertionError` raised inside the code under test |
| collection error | pytest could not import or collect the file |
| timeout | the suite exceeded the package's time limit |
| infrastructure | the sandbox failed or pytest died without a report: a harness error (exit 2), never a verdict |

The class comes from structured data recorded by a pytest plugin inside the
sandbox (exception type, test phase, and the file of the innermost traceback
frame inside the workspace), not from parsing output.

World 3 only runs when World 2 passed: mutants are judged against a working
reference or not at all.

A mutant ends in exactly one of four states, and they are never merged:

| State | Meaning | Effect on the verdict |
|---|---|---|
| killed | a test that passed in World 2 fails with the mutant | counts for the package |
| survived | the mutant ran and every test still passed | rejects the package unless waived |
| equivalent | the mutant compiles to bytecode identical to the reference, so it cannot change behaviour | excluded, with the evidence recorded |
| invalid | no verdict: the patch does not apply, or the tests timed out | rejects if the mutant was declared by the package; otherwise excluded and reported |

Identical bytecode is the only evidence of equivalence the harness accepts.
A survivor that merely looks harmless stays a survivor. The mutation score is
killed / (killed + survived).

Mutants come from three places. **Contract** mutants remove the guard of an
attached guard contract (see below). **Declared** mutants are patches shipped in the
package. **Generated** mutants are made by the harness with five small
operators (boundary swap, comparison negation, `and`/`or` swap, `not`
removal, forcing a condition true or false), applied only to lines the
reference patch added. The package author cannot choose which generated
mutants run. A survivor rejects the package unless the manifest carries a
waiver with a justification and an approver, and a waiver is written into the
evidence as a manual intervention.

## Contract archaeology

    unwritten contracts extract --repo <git repo> --profile <profile.json> [--hypotheses <file>]
    unwritten contracts attach <package> --map <directory written by extract>

`extract` builds the Repository Contract Map for one pinned commit. A contract
is **verified** only by execution; everything else is a **hypothesis** or is
**refuted**.

| Source | Verified when |
|---|---|
| an existing test | it passes at the pinned commit in two runs **and** fails under at least one injected fault |
| a guard clause in the source (an `if` that raises) | removing the guard makes a baseline-passing test fail; those tests become its probes |
| a supplied hypothesis with probe code (from a model or a person) | its probe passes twice and fails under at least one injected fault. A probe that fails by assertion refutes the claim; a probe that crashes, cannot be imported or is insensitive to every fault leaves it a hypothesis |

The injected faults are the same single-token mutants World 3 uses, applied to
every source line, and they end in the same four states (an injected fault
whose tests time out is called unexecutable). A guard whose removal survives
is reported as exactly that: no test noticed. The harness does not decide
whether such a guard is an untested rule or has no observable effect. A
supplied hypothesis keeps its `origin` label in every state, including
verified.

Each verified contract records its probes and the faults they detected, so the
claim can be rechecked against the raw logs.

Contract ids are stable for a commit and are what packages reference:

    test:<test file>::<test function>
    guard:<source file>:<symbol>:<digest of the guard's condition and exception>
    hyp:<id given by the proposer>

A guard id depends on what the guard says, not on its position, so adding
another guard to the same function does not rename it.

`attach` selects the verified contracts that concern files a task's reference
patch touches, copies their probes into the package and writes
`contracts.json`. `verify` then rejects a reference that breaks one
(`W2_CONTRACT_VIOLATED`), and adds one World 3 mutant per guard contract that
removes the guard.

### Changing a contract on purpose

A task that intentionally changes existing behaviour declares it in
`task.json`:

    "contract_overrides": [{
        "contract_id": "guard:shopcart/catalog.py:Product.__post_init__:c1c033fc",
        "reason": "refund lines carry negative prices",
        "new_behaviour": "a product may have a negative price",
        "replacement_tests": ["tests/feature/test_refund_lines.py::test_negative_price_is_allowed"]}]

An override is checked like any other claim:

- the contract must be attached to the package (`OVERRIDE_UNKNOWN_CONTRACT`);
- it must hold at the base commit and at least one of its probes must fail
  with the reference, otherwise nothing justifies the exemption
  (`OVERRIDE_NOT_NEEDED`);
- every replacement test must be a feature test, so it fails by assertion at
  base and passes with the reference (`OVERRIDE_REPLACEMENT_MISSING`);
- only the probes of the named contract are exempt. Every other attached
  contract is still enforced, and the evidence lists how many were attached
  and preserved.

Overrides are written into the evidence as manual interventions. The task
generator that will write them is a later milestone; the verifier side is
implemented and tested.

On the pilot (`pilot/profile.json`, with the recorded hypotheses in
`pilot/hypotheses.json`): 34 contracts, of which 28 verified, 4 hypotheses and
2 refuted. Of 45 injected faults, 44 are killed and 1 survives: removing the
`sku not in self._lines` check in `Cart.remove` fails no test. The output is
committed in `evidence/contract-map-unwritten-pilot/`.

Language support goes through `src/unwritten/adapters/` (source files,
symbols, guards, mutants). Python is the only adapter; test execution is
pytest only.

## Holding a task to its statement (schema 0.2)

A solver sees only `statement.md`. Schema 0.2 packages therefore declare, in
`task.json`, what the statement promises, and the harness checks the rest of
the package against it. Schema 0.1 packages are unaffected.

    "acceptance_criteria": [{"id": "AC-1", "text": "..."}]
    "test_criteria":       {"tests/feature/test_x.py::test_name": ["AC-1"]}
    "public_interface":    [{"module": "shopcart.cart", "name": "Cart.update_quantity",
                             "kind": "function",
                             "signature": "update_quantity(self, sku, quantity)"}]

**Static gates** run first and execute nothing:

- the statement must contain every criterion id and every declared name with
  its module and signature;
- every feature test names a criterion and every criterion has a test;
- feature tests may import only names that exist at the base commit or are
  declared, and never private names;
- a declared name must be new, or changed in signature.

**World 1** then accepts a second kind of failure. A feature test may fail on
a missing name when all of these hold: the exception is an `ImportError`,
`AttributeError` or unexpected-argument `TypeError`; it was raised by test
code; and the missing name matches a declared entry. The missing name is read
from the exception object (`ImportError.name`, `AttributeError.name` and
`.obj`); only the unexpected-argument case has to be parsed from the message,
and the evidence says so. A missing name that is not declared is rejected:

| Code | Meaning |
|---|---|
| `W1_FEATURE_USES_UNDECLARED_INTERFACE` | a test depends on a name the statement never mentions |
| `W2_DECLARED_INTERFACE_NOT_IMPLEMENTED` | the reference does not define a declared name |
| `W2_DECLARED_SIGNATURE_MISMATCH` | the reference's signature differs from the statement |
| `GATE_STATEMENT_MISSING_CRITERION`, `GATE_STATEMENT_MISSING_INTERFACE` | the manifest declares something the statement does not say |
| `GATE_TEST_WITHOUT_CRITERION`, `GATE_CRITERION_WITHOUT_TEST`, `GATE_UNKNOWN_TEST` | traceability is broken |
| `GATE_TEST_USES_PRIVATE_NAME`, `GATE_TEST_IMPORTS_UNDECLARED_NAME` | a test is coupled to implementation detail |
| `GATE_INTERFACE_NOT_NEW` | a declared name already exists unchanged |

`packages/pilot-cart-update-quantity` is the hand-written schema 0.2 example:
a task that adds one public method. All 15 of its feature tests fail at the
base commit on that declared method, and pass with the reference.

## Variation

    unwritten variation <package> <package> ... --repo <git repo>

Tasks are compared by what they change and test. Wording decides nothing.

- **Fingerprint**, computed from the reference patch: files, symbols touched,
  attached and overridden contracts, and categories derived from the change
  (new public name, new type, signature change, input validation, change to
  existing behaviour, contract change).
- **Distance**: one minus a weighted Jaccard similarity over those sets.
- **Test overlap**: the share of one task's feature tests that pass with the
  other task's reference. Every feature test fails at the base commit, so a
  passing test is behaviour the other task already delivers. Full overlap
  means one task subsumes the other.

A pair is a duplicate when the distance is below the threshold (0.25, a chosen
value) or either task subsumes the other; the command then exits 1. The two
pilot tasks are 0.85 apart with no test overlap. A copy of one task with a
completely rewritten statement is caught at distance 0 with full overlap in
both directions.

## Validated and released

    unwritten status  <package> [--evidence <dir>]
    unwritten release <package> --evidence <dir> --reviewer <name> --approve

| State | Meaning |
|---|---|
| draft | no accepted evidence for the package as it is now |
| validated | `verify` accepted exactly this content |
| released | validated, and a named person approved the statement and tests |
| release_invalid | released once, but the content has changed since |

`release` refuses unless the evidence is accepted and was produced for the
current content. It writes `release.json` with the reviewer, a timestamp and
hashes of the statement, the tests and the whole package. Any later change
makes `status` report `release_invalid` and name what changed. The reviewer's
name is whatever was typed; it is not authenticated. No package in this
repository has been released: that is a human act.

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
| `packages/pilot-cart-update-quantity` | hand-written schema 0.2 task that adds a public method | accepted |

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
    contracts.json          optional; verified contracts this task must preserve
    tests/contracts/        optional; supplied probes of those contracts

## Evidence

Each verification writes a directory:

    evidence.json           verdict, reasons, environment, both runs in full, fingerprints
    logs/run<N>/<world or mutant>/<suite>.log and .json    raw pytest output and per-test records
    mutants/<id>.diff       what each mutant changed

`evidence/` holds the evidence from verifying the four shipped packages on
the Docker runner, the pilot's contract map and the variation report for the
two valid tasks, with their raw logs. A test
checks that every file these documents refer to exists.

Every rejection reason carries a code, the world that raised it and the tests
or mutants responsible.

| Code | Meaning |
|---|---|
| `PACKAGE_INVALID` | the package does not follow the schema |
| `W1_FEATURE_PASSES_AT_BASE` | a feature test passes before the feature exists |
| `W1_FEATURE_RUNTIME_ERROR` | a feature test crashes instead of asserting |
| `W1_FEATURE_COLLECTION_ERROR` | a feature test file cannot be imported or collected at base |
| `W1_FEATURE_NOT_EXECUTED` | a feature test is skipped or marked as an expected failure |
| `W1_CONTRACT_PROBE_NOT_PASSING` | an attached contract does not hold at the base commit |
| `W1_NO_FEATURE_TESTS`, `W1_NO_REGRESSION_TESTS` | nothing to judge |
| `W1_REGRESSION_FAILS_AT_BASE` | a regression test fails on the untouched commit |
| `REFERENCE_PATCH_DOES_NOT_APPLY`, `REFERENCE_MODIFIES_TESTS` | the reference patch is unusable or edits the repo's tests |
| `W2_FEATURE_FAILS_WITH_REFERENCE` | the reference does not satisfy the feature tests |
| `W2_REGRESSION_BROKEN`, `W2_TEST_SET_DIFFERS` | the reference changes existing behaviour or the collected tests |
| `W2_CONTRACT_VIOLATED` | the reference breaks a contract verified at the base commit |
| `W3_MUTANT_SURVIVED` | a wrong implementation passes every test |
| `W3_NO_MUTANTS`, `W3_MUTANT_INVALID`, `W3_WAIVER_UNKNOWN_MUTANT` | no mutant reached a verdict, a declared mutant gave none, or a waiver names no mutant |
| `OVERRIDE_UNKNOWN_CONTRACT`, `OVERRIDE_NOT_NEEDED`, `OVERRIDE_REPLACEMENT_MISSING` | a declared contract override is not backed by results |
| `EXECUTION_TIMEOUT` | a suite exceeded the package's time limit |
| `REPEATABILITY_MISMATCH` | two runs on identical inputs disagreed |

## Development

    python -m pytest                  # everything; Docker tests skip, loudly, without a daemon
    python -m pytest -m "not docker"  # local runner only

Source layout: `src/unwritten/` holds `package` and `profile` (loading),
`patches`, `workspace`, `execution`, `mutation`, `classify`, `worlds` (one
class per world, run in `PIPELINE` order), `verify` (orchestration and
verdict), `gates` and `interface` (schema 0.2 checks), `variation`, `release`,
`contracts/` (extract, attach, hypotheses), `adapters/`, `runners/`
and the two files that execute inside the sandbox, `runners/_inner/`. The
harness has no runtime dependencies outside the standard library.

## Known limits

- Python repositories tested with pytest only.
- Declared-interface attribution needs Python 3.12 or later inside the sandbox
  for `from module import name` failures (`ImportError.name_from`).
- A declared class or attribute is checked for existence only; signatures are
  compared for functions and methods.
- The variation threshold and weights are chosen values. Removed-only hunks do
  not contribute symbols to a fingerprint.
- The harness proves that tests detect the feature's absence and detect
  mutants. It does not prove the tests match the written statement, or that
  the task is solvable from the statement alone. That needs a solver that
  sees only the statement (a planned fourth world).
- Generated mutants cover conditions and comparisons on added lines. Wrong
  arithmetic, wrong return values and omitted code paths are covered only by
  declared mutants.
- Equivalence is only detected when the bytecode is identical. A mutant that
  is equivalent for a subtler reason stays a survivor and needs a human
  waiver, which is recorded as a claim, not as proof.
- The sandbox trusts the test report written from inside the container. A
  hostile repository could forge it.
- No model proposes hypotheses yet. The hypothesis file format is the
  interface; the pilot's file is hand-written and labelled as such.
- A verified contract means its probe passes and is sensitive to faults. It
  does not mean the probe tests what the description says; that link is still
  read by a person.
- Guard discovery finds `if ...: raise` only. Contracts expressed as return
  values, ordering or state changes are found only through tests and supplied
  hypotheses.
- A contract's probe list is capped at 12 tests.
- An override proves the old contract no longer holds and the replacement
  tests pass. Whether `new_behaviour` describes those tests accurately is
  still read by a person.
- Fault injection runs the whole test suite once per fault, which will need
  test selection on large repositories.
- Runs are compared within one machine and one image. Nothing here claims
  identical results across machines.
