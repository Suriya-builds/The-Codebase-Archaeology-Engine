# M3 proposal: The Challenge Factory

Status: **approved by TEAM EDITH on 14 November 2026, with the decisions in
section 0.** Implementation status is tracked in the README, not here.
Baseline: M1 and M2 as accepted at commit `7ccf936`.

## 0. Approved decisions

These override anything below that says otherwise.

1. **World 1 extension: approved.** A missing public name counts as a
   legitimate feature failure only when its exact interface is declared in
   the task statement: module, public name and expected signature. The
   failure must be attributed to that name, there must be no unrelated
   collection or import failure, and the test must pass against the
   reference. Existing packages keep their behaviour. Dedicated regression
   tests come before any use in generation.
2. **Validated and released: approved.** `validated` means every automated
   check passes. `released` adds a human review with explicit approval,
   storing reviewer identity, timestamp and hashes of the reviewed statement
   and tests. Any later modification invalidates the release. Only released
   tasks enter the submission.
3. **Models: `claude-opus-5-5` for specification and implementation;
   `claude-sonnet-5-5` for the test author, as an experiment.** Inputs are
   isolated by role. Every call records exact model id, input, prompt and
   output hashes, token usage, decisions and actual cost. Separate calls are
   not assumed to give independent correctness. No temperature-based
   reproducibility; full recording and replay instead.
4. **Budget: up to 100 US dollars, conditionally.** Before spending: check
   for organiser-provided credits, estimate from a small pilot, implement
   usage accounting and a hard limit. Credentials are never committed.
5. **Repositories:** the shopping-cart pilot, a task-management application
   and a backend API application, with different behavioural and
   architectural characteristics. Licence, test coverage, dependencies and
   reproducibility are checked for each.
6. **World 4: approved in principle.** Pluggable solver, three attempts per
   task, no exposure of reference patches, hidden tests, contract maps or
   generation prompts. It is evaluation evidence, not an automatic validity
   gate. Not implemented until its protocol is documented and tested.
7. **Organiser compatibility.** The official interface, supplied repository
   and execution rules are authoritative. A compatibility checkpoint comes
   before the generation pipeline is finalised. Python and pytest are not
   assumed to be the target; language adapters stay modular.

Non-negotiable: the generator is not its own judge; test generation is
isolated from the reference; every criterion maps to a test and every test to
a criterion; variation is behavioural; overrides stay explicit and auditable;
no fabricated metrics; only released tasks are submitted; M1 and M2 stay
stable; the frontend is secondary.

Implementation order: the deterministic half (World 1 extension, schema,
declared-interface validation, traceability, variation, override
integration), then generation roles and replay, then repository
generalisation and World 4, then the evidence viewer.

## 1. What M3 has to prove

M1 and M2 can judge a task package. M3 has to produce packages worth
judging, without the producer grading its own work. The design rule is:

> A model may propose. Only execution decides.

Every model output in M3 is treated the way M2 treats a hypothesis: recorded
with its origin, then accepted, rejected or left open strictly by running
code.

## 2. Assumptions in the brief that I think are weak

These shape the design, so they come first.

1. **"Generated independently" is weaker than it sounds.** Two calls to the
   same model family share the same misreadings. Separate prompts give
   *information isolation* (the test author has never seen the reference),
   not statistical independence. The proposal enforces isolation in code and
   says so in the evidence; it does not call the result independent
   validation. Real independence comes from the deterministic engines, from
   World 4, and from a recorded human review.

2. **M1 currently rejects any feature that adds a new function or class.** A
   feature test that calls a function which does not exist yet fails at the
   base commit with `AttributeError` or `ImportError`, which World 1 classes
   as a runtime error. The pilot task avoided this by going through existing
   interfaces. Real features often cannot. This needs one deliberate change
   to World 1 (section 6.1). It is the only change to M1 this proposal asks
   for.

3. **Model-assigned scores are not a ranking.** "Feasibility 8/10" from a
   model is noise. Opportunities are ranked on measurable properties, and
   feasibility is whatever survives the pipeline. The rejection funnel is
   reported, not hidden.

4. **Generation cannot be made repeatable by settings.** Current Claude
   models reject `temperature` and other sampling parameters. Repeatability
   has to come from recording every request and response and replaying them.
   Verification stays repeatable as it is today.

5. **No machine check proves a statement means what its tests check.** The
   traceability and coupling checks below narrow the gap. They do not close
   it. A task therefore has two terminal states: `validated` (every machine
   check passed) and `released` (a named person has also read the statement
   against the tests). Only released tasks go in the submission set.

6. **The pilot cannot demonstrate variation.** It has five source files.
   Meaningful variation needs at least two further repositories of realistic
   size, and the organisers' supplied repository is still unknown to us.

7. **Volume is the wrong target.** I propose 8 to 12 released tasks across
   three repositories, with the full funnel (proposed, generated, validated,
   released, and why each loss happened) as part of the evidence.

## 3. Architecture

Four roles produce artefacts. None of them judges. Each role is a separate
model call whose input bundle is assembled by code, hashed and logged, so
what a role could see is a recorded fact.

| Role | Sees | Never sees | Produces |
|---|---|---|---|
| Opportunity proposer | contract map, source digest, accepted tasks' fingerprints | | candidate opportunities citing contract ids |
| Specification writer | one opportunity, the cited contracts, relevant source | | statement, acceptance criteria, declared interface, contract overrides |
| Test author | statement, declared interface, base source | reference implementation | feature tests, each tagged with the criteria it checks |
| Implementer | statement, declared interface, base source, feature test *results* | | changed files in full (the harness computes the patch) |
| Adjudicator (only on disagreement) | statement, one failing test, its failure | | a ruling that cites the statement: test wrong, reference wrong, or statement ambiguous |
| Test hardener (only on surviving mutants) | statement, tests, surviving mutant diffs | | additional tests, tagged `origin: hardening` |

Information flows one way: tests may inform the implementer, as in
test-driven development; the reference never reaches the test author. The
hardener is the one role that sees implementation detail, which is why its
tests are tagged and get extra scrutiny (section 6.4).

## 4. Data flow

```
 repo @ commit ──► M2 extract ──► contract map (verified / hypothesis / refuted)
                                        │
                        ┌───────────────┘
                        ▼
              opportunity proposer  ──►  candidates
                        │           deterministic checks: cited contracts exist
                        │           and are verified; cited symbols exist;
                        │           not a near-duplicate of an accepted task
                        ▼
              specification writer  ──►  statement.md, criteria, declared
                        │                interface, overrides
            ┌───────────┴───────────┐
            ▼                       ▼
       test author             implementer          (isolated from each other;
            │                       │                 implementer gets test
            ▼                       ▼                 results, not vice versa)
      feature tests           changed files ─► reference.patch
            └───────────┬───────────┘
                        ▼
              package assembly + M2 attach
                        ▼
              static gates (section 6)  ── fail ──► reject, reason recorded
                        ▼
              M1 verify: Worlds 1-3, twice
                 │            │
                 │       disagreement ──► adjudicator ──► bounded repair
                 │       survivors    ──► hardener    ──► re-verify
                 ▼
              variation gate (section 7)
                        ▼
              VALIDATED ──► [World 4, once approved] ──► human review ──► RELEASED
                        │
              generation ledger: every request, response, decision, repair
```

A repair loop is bounded (proposed: two rounds per task). If the adjudicator
rules the statement ambiguous, the statement is rewritten and every
downstream artefact is regenerated; nothing is patched in place. Every round
is recorded. A task that needed repair says so in its evidence.

## 5. Task package schema 0.2

Schema 0.1 stays valid. Additions:

```
task.json
  acceptance_criteria: [{id: "AC-1", text: "..."}]
  public_interface:    [{kind: "function", module: "shopcart.discounts",
                         name: "Eligibility", signature: "..."}]   # new or changed
                                                                     # public names
  affected:            {files: [...], symbols: [...]}               # computed
  test_criteria:       {"tests/feature/test_x.py::test_a": ["AC-1"]}
  contract_overrides:  [...]                                        # as in M2
  status:              "validated" | "released"
  review:              {reviewer, date, notes}                      # released only
statement.md           fixed sections: Background, Requirement, Acceptance
                       criteria, Interface, Must keep working, Out of scope
generation/
  ledger.json          per call: role, model requested and model that served,
                       effort, prompt template id and hash, input bundle hash,
                       response hash, token usage, request id, stop reason,
                       outcome (kept / repaired / discarded) and why
  calls/               the full request and response of every call, for replay
variation.json         the task's fingerprint (section 7)
```

Export writes two bundles: **public** (statement, repository identity and
commit, environment) and **private** (reference, tests, mutants, contracts,
ledger, evidence). World 4 and any external evaluator get only the public
one.

## 6. Test independence strategy

Four mechanisms, in order of strength. All but 6.4's last step are
deterministic.

### 6.1 Declared interface, and the one change to World 1

The statement must declare every public name a solver is expected to add or
change. World 1 then treats a feature test's failure at the base commit as
valid in two cases instead of one:

- a behavioural assertion (today's rule), or
- a missing-name error (`ImportError`, `AttributeError`, `TypeError` for an
  unexpected argument) whose missing name **is in the declared interface**.

A missing name that is *not* declared is rejected with a new code,
`W1_FEATURE_USES_UNDECLARED_INTERFACE`. The decision uses the exception's
structured `name` attribute, not message text.

This is the requested rule made mechanical: a test that depends on a name
the statement never mentions cannot pass World 1.

### 6.2 Static coupling gate

Before any execution, each feature test is parsed. Every name it imports
from the repository, and every attribute it reads on such a name, must
exist at the base commit or be in the declared interface. Private names
(leading underscore) are rejected outright. This catches coupling that 6.1
cannot, for example a test that passes at base for unrelated reasons.

### 6.3 Criterion traceability

Every acceptance criterion needs at least one feature test, and every
feature test must name at least one criterion. An untested criterion or an
unexplained test rejects the package. The mapping is declared by the test
author and checked for completeness by code; whether a test truly checks
its criterion is part of the human review.

### 6.4 Isolation, disagreement and hardening

- The test author's input bundle is built by code and contains no reference
  material; its hash is in the ledger.
- If isolated tests and the reference disagree, that is information. The
  adjudicator rules against the statement's text, and an ambiguous
  statement is rewritten.
- Tests added by the hardener have seen mutant diffs, so they could encode
  the reference's shape. They are tagged, must pass gates 6.1 to 6.3, and
  are reported separately. Whether they overfit is exactly what World 4
  measures: an independent solution must pass them too.

## 7. Variation measurement

Each validated task gets a fingerprint computed from artefacts, not prose:

| Dimension | Source |
|---|---|
| files touched | reference patch |
| symbols touched | language adapter applied to patch hunks |
| contracts in play | attached and overridden contract ids |
| behavioural categories | counted from the patch: guards added, public names added, signatures changed, branches added, data fields added |
| size | changed lines, feature tests, criteria |

Two deterministic comparisons:

- **Distance.** A weighted Jaccard distance over the set-valued dimensions.
  A candidate closer than a threshold to an accepted task is rejected as a
  near-duplicate. The full matrix is reported.
- **Behavioural subsumption.** Task A's feature tests are run against task
  B's reference. If they pass, B already delivers A's behaviour, whatever
  the two statements say. This is the strongest duplicate signal available
  and costs one sandbox run per pair.

Statement text similarity (token overlap, no embeddings) is reported for
information only and never decides.

## 8. World 4: Blind Solver (protocol only)

**Purpose.** Evidence that a task can be solved from its public statement
alone, and that its hidden tests accept a correct solution that was written
without sight of the reference.

**Inputs to the solver.** The repository at the pinned commit (including
its own tests), `statement.md`, and the environment definition. Nothing
else. The harness builds the solver workspace from the public bundle only;
the private bundle is never mounted, and a test asserts that.

**The solver.** Anything that takes a repository directory and a statement
and returns a patch. The interface is a command, so the team's agent, a
different vendor's agent or a judge's own agent can be plugged in. For our
runs: a Claude agent with file and shell tools, executing inside the
existing sandbox with no network, started in a fresh context with a
configuration recorded in the evidence.

**Procedure.** k attempts per task (proposed k = 3), each with a fixed
token and time budget. Each returned patch replaces the reference in a
World 2 run: hidden feature tests, regression tests and attached contracts
(with the task's declared overrides).

**Outcomes.**

| Outcome | Meaning | Effect |
|---|---|---|
| `solved` (at least one attempt passes everything) | solvable from the statement; tests accept an unseen solution | recorded; supports release |
| `failed_on_hardening_tests_only` | hidden tests may be fitted to the reference | needs review; the hardening tests are examined |
| `failed_on_undeclared_interface` | coupling that gates 6.1 and 6.2 missed | task rejected; the gate gets a regression test |
| `failed_on_regression_or_contract` | the solver broke existing behaviour | counts against the solver, not the task |
| `no_valid_patch` / budget exhausted | inconclusive | recorded; not evidence either way |

**What World 4 does not show.** Zero of k solved does not prove a task
invalid; it may be hard. One solved does not prove the statement is clear
to a different agent. A solver from the same model family as the generators
shares their blind spots, and the evidence states which model solved.

**Leak controls.** No test names or file names from the private bundle
appear in the statement (checked by string search). Solver transcripts are
hashed and stored. The solve rate is reported per task as a difficulty
signal.

World 4 is not built until this protocol is approved.

## 9. API key requirements and model usage

**What is needed from the team**

- An Anthropic API key with access to `claude-opus-5-5`, provided through
  this environment's credential form for `api.anthropic.com`. It is never
  written to the repository, logs or evidence.
- Confirmation that the key's organisation allows 30-day data retention is
  not required for Opus; it would be for Fable models.

**How models are used**

- Default model for every role: `claude-opus-5-5`, with adaptive thinking
  and effort recorded per call.
- A decision for the team: running the test author on a different model
  from the implementer (for example `claude-sonnet-5-5`) reduces shared
  blind spots at some cost in test quality. I recommend trying it on the
  pilot and keeping it only if validated-task yield does not drop.
- Structured outputs for every role, validated against a schema before use.
  Forced tool choice is not available on current models, so schemas, not
  tool calls, carry the structure.
- Models return complete files, not diffs; the harness computes the patch.
  Model-written diffs are a frequent source of unusable output.
- Refusals are checked on every response. Server-side fallback is enabled,
  and the ledger records which model actually served each call.
- Repeatability: every request and response is stored. `--replay` rebuilds
  a package from the ledger with no API call, and a test proves the replay
  is byte-identical.
- Prompt caching on the repository digest, which is the large stable prefix
  shared by every call for a repository.

**Dependencies introduced.** The official `anthropic` SDK and Pydantic, as
an optional install (`unwritten[generate]`). `unwritten verify` and
`unwritten contracts` keep working with the standard library alone.

**Cost, as a rough estimate to be replaced by measured figures.** Six to
ten calls per task attempt, in the order of 50,000 input and 15,000 output
tokens, so well under one US dollar per attempt at current Opus prices.
With rejections, a few dollars per released task. World 4 agent runs are
larger, in the order of a few dollars per attempt. A budget of 100 US
dollars should cover M3 and World 4 with room to spare.

## 10. Acceptance criteria for M3

1. `unwritten generate` produces a schema 0.2 package from a repository and
   its contract map without manual editing.
2. Every opportunity cites verified contract ids that exist in the map;
   citing a hypothesis or an unknown id is rejected by code.
3. The test author's input bundle provably contains no reference material
   (bundle manifest and hash in the ledger; a test checks it).
4. A package whose test uses an undeclared name is rejected with
   `W1_FEATURE_USES_UNDECLARED_INTERFACE`; one that uses a declared new
   name is accepted. Both shown with hand-written fixtures.
5. A criterion with no test, and a test with no criterion, each reject the
   package.
6. Every generated package is judged by the unchanged M1 and M2 engines
   (apart from the World 1 rule in 6.1), and no model output influences a
   verdict.
7. The ledger records model, prompts, decisions, repairs, commit and manual
   interventions, and `--replay` reproduces the package byte for byte with
   no network.
8. The variation gate rejects a deliberately duplicated task, by distance
   and by subsumption, and the matrix is reported for all accepted tasks.
9. At least three repositories, at least eight validated tasks, with the
   full funnel reported and every rejection carrying a reason code.
10. A generated task that intentionally changes a contract goes through the
    override mechanism and is accepted with its evidence.
11. All M1 and M2 tests still pass on both runners.

## 11. Schedule to 6 December

Today is 14 November: 22 days.

| Dates | Work | Needs |
|---|---|---|
| 15-18 Nov | **M3a, deterministic half.** Schema 0.2, World 1 declared-interface rule, coupling gate, traceability, variation fingerprint and subsumption. Tested with hand-written fixtures, no model. | approval of this proposal |
| 19-23 Nov | **M3b, generation.** Roles, ledger, replay, adjudication and hardening loops. First generated tasks on the pilot. | API key by 18 Nov |
| 24-26 Nov | **M3c, breadth.** Two real open-source Python repositories; funnel and variation report; M3 acceptance run. | choice of repositories |
| 27-29 Nov | **World 4.** Solver interface, public-bundle isolation, outcome classification. | approval of the protocol |
| 30 Nov-2 Dec | **Evidence viewer.** Read-only, generated from evidence files. | |
| 3-5 Dec | Freeze, final evidence, documentation, demonstration recording. | organiser submission format |
| 6 Dec | Submission, with a day of slack. | |

If the schedule slips, cut in this order: the viewer becomes a static HTML
report; World 4 is demonstrated on a subset of tasks; the third repository
is dropped. The deterministic gates and the ledger are never cut.

## 12. Mandatory versus differentiating

| Part | Track 1 requirement | Role |
|---|---|---|
| Statement with acceptance criteria | clear, achievable requirements | mandatory |
| Reference implementation that passes | working reference in the repo's stack | mandatory |
| Feature and regression tests | required deliverable | mandatory |
| Commit pinning, package export | packages tagged to a starting version | mandatory |
| Multiple repositories | repeatability | mandatory in practice |
| Variation fingerprint and subsumption test | meaningful variation (20%) | mandatory to score; subsumption is a differentiator |
| Three-World validation with generated mutants | validity evidence (25%) | differentiator |
| Contract grounding and overrides | consistency with the repository | differentiator |
| Declared-interface and coupling gates | solvability | differentiator |
| Role isolation with a hashed bundle manifest | validity | differentiator |
| Ledger and replay | repeatability (25%) | differentiator |
| World 4 | solvability evidence | differentiator; highest value after the gates |
| Viewer | communication (15%) | supporting |

## 13. Still missing from the organisers

Unchanged since phase 0 and now more urgent: the Track 1 statement
verbatim, the supplied repository or at least its language and test
framework, the required package format, whether model calls are allowed
during judging, and the submission format. If the supplied repository is
not Python with pytest, the adapter and runner work moves ahead of
everything in section 11.
