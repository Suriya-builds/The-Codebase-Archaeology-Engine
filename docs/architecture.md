# UNWRITTEN: how M1 and M2 fit together

This note covers what is built: the verification harness (M1) and contract
archaeology (M2). Task generation (M3) and the interface are not built yet.

## The problem being solved

A generated coding task has three parts that must agree: the requirement
text, a reference implementation, and tests. If one generator writes all
three, they can agree with each other and still be wrong. UNWRITTEN therefore
accepts nothing on a model's say-so. Every decision comes from running code
against an exact commit, and every decision leaves evidence a reviewer can
read.

M1 answers: *is this task package valid?*
M2 answers: *what does this repository already promise, and does the task
keep those promises?*

## One picture

```
                      repository @ pinned commit
                                 |
          +----------------------+----------------------+
          |                                             |
   M2  contracts extract                          task package
          |                                  (statement, reference patch,
   static pass (language adapter):            feature tests, mutants)
     symbols, guard clauses, faults                     |
   baseline run x2                                      |
   one run per injected fault                           |
          |                                             |
   contract-map.json  ---- contracts attach ---->  contracts.json
   (verified / hypothesis / refuted)               + contract probes
                                                        |
                                                 M1  verify
                                                        |
                              World 1  commit as is: feature tests fail by
                                       assertion; regression tests and
                                       contract probes pass
                              World 2  + reference patch: feature tests pass;
                                       regression unchanged; contracts hold
                              World 3  + each mutant: some test must fail
                              Repeat   everything again; results must match
                                                        |
                                              evidence.json + logs
                                          accepted / rejected, with reasons
```

Both halves share the same three building blocks, which is why M2 added
little new machinery:

- **Sandbox execution.** A workspace is exported from the pinned commit,
  patched, and run in a fresh container with no network, a read-only root
  filesystem, no capabilities and a non-root user. A pytest plugin inside the
  container records, per test, the outcome, the exception type and the file
  where it was raised.
- **Fault injection.** Five single-token operators (boundary swap, comparison
  negation, `and`/`or` swap, `not` removal, forcing a condition) produce
  mutants. M1 applies them to the lines a reference patch adds. M2 applies
  them to the whole repository.
- **Failure classification.** Assertion, runtime error, collection error,
  timeout and infrastructure failure are kept apart. Only a behavioural
  assertion counts as proof that a feature is absent or a claim is refuted.
  An infrastructure failure is a harness error, never a verdict.

## M1: the Three-World Test

| World | Built from | Must hold |
|---|---|---|
| 1 Broken | pinned commit | every feature test fails through an assertion; regression tests pass |
| 2 Correct | + reference patch | every feature test passes; regression results identical to World 1 |
| 3 Deception | + one mutant at a time | every mutant fails at least one test that passed in World 2 |
| Repeat | all of it, again | identical outcomes, failure classes and mutant results |

A mutant ends as killed, survived, equivalent or invalid. It is called
equivalent only when it compiles to the same bytecode as the reference. A
mutant that merely looks harmless remains a survivor, and a survivor rejects
the package unless a named person waives it; the waiver is recorded.

Mutants come from the package author (declared), from the harness (generated
on the patch's lines, so the author cannot choose them), and from M2 (one per
attached guard contract).

## M2: contract archaeology

A contract is a statement about existing behaviour with an executable probe.
Its status is decided only by execution:

| Source | Verified when |
|---|---|
| an existing test | it passes at the pinned commit twice and fails under at least one injected fault |
| a guard clause (`if ...: raise`) | removing the guard fails a baseline-passing test; those tests become its probes |
| a supplied hypothesis with a probe, from a model or a person | the probe passes twice and fails under at least one injected fault |

A probe that fails by assertion **refutes** the claim. A probe that crashes,
cannot be imported, or reacts to no fault leaves it a **hypothesis**. The
second condition, sensitivity to a fault, is what stops a probe that asserts
nothing from verifying anything. A hypothesis keeps the label of whoever
proposed it in every state.

A guard whose removal no test notices is reported as exactly that. It may be
a rule nobody tests, or a check with no effect; the harness does not guess.
On the pilot, the repository's own tests leave three guards undetected. One
of them, "a price cannot be negative", becomes a verified contract once a
supplied probe exercises it.

Contract ids (`test:...`, `guard:<file>:<symbol>:<digest>`, `hyp:...`) are
identical across repeated extractions and across runners, and are what
packages reference.

## Where the two meet

`contracts attach` copies into a package the verified contracts that concern
the files its reference patch touches. From then on `verify` treats them as
regression protection:

- World 1 requires each attached contract to hold at the pinned commit.
- World 2 rejects a reference that breaks one, naming the contract
  (`W2_CONTRACT_VIOLATED`).
- World 3 adds a mutant that removes each attached guard.

The value of this link is shown by one test: a reference patch that quietly
deletes the negative-price check is **accepted** by M1 alone, because no
existing test covers it, and **rejected** once the contract map is attached.

A task that changes a contract on purpose declares an override: the contract
id, the reason, the new behaviour and replacement tests. The verifier accepts
it only if the contract held at the base commit, really fails with the
reference, and the replacement tests fail before and pass after. Only the
named contract is exempt; all others stay enforced, and the evidence counts
them.

## What a judge can inspect

- `packages/`: one valid task and two deliberately invalid ones.
- `evidence/<package>/evidence.json`: the verdict, every reason with the
  responsible tests or mutants, both runs in full, contract results.
- `evidence/<package>/logs/` and `mutants/`: raw pytest output, per-test
  records and the diff of every mutant.
- `evidence/contract-map-unwritten-pilot/`: every contract with its probes
  and the faults they detect, every fault with its state.
- `python -m pytest`: reproduces all of the above on both runners.

## What this does not establish

- That a task's written statement matches its tests, or that the task can be
  solved from the statement alone. A solver that sees only the statement (a
  fourth world) is planned for that.
- That a verified contract's description matches what its probe checks. The
  probe is proven to pass and to be sensitive; the wording is read by a
  person.
- Anything beyond Python and pytest. Analysis goes through a language adapter
  interface, but only the Python adapter exists.
- Anything about model-written tasks. No model is called anywhere yet; the
  pilot's task and hypotheses are hand-written and labelled as such.
