"""Command line interface.

    unwritten verify <package> --repo <git repository> [--runner docker|local]
    unwritten contracts extract --repo <git repository> --profile <profile.json>
    unwritten contracts attach <package> --map <contract map directory>

Exit status: 0 accepted, 1 rejected, 2 the harness could not complete.
"""
import argparse
import os
import sys
from pathlib import Path

from unwritten.contracts import MAP_FILE, attach_contracts, extract_contracts
from unwritten.errors import HarnessError
from unwritten.profile import load_profile
from unwritten.runners import make_runner
from unwritten.verify import EVIDENCE_FILE, verify_package

_SHOWN_ITEMS = 8


def _world_line(world):
    s = world.summary
    if world.status == "not_run" or not ({"feature_tests", "mutants_executed",
                                          "acceptance_criteria"} & set(s)):
        text = s.get("note", "")  # skipped, or stopped before anything ran
    elif world.name == "gates":
        text = (f"{s['acceptance_criteria']} criteria, {s['feature_test_functions']} feature "
                f"tests, {s['declared_interface']} declared names: consistent"
                if world.status == "passed" else "")
    elif world.name == "world1":
        text = (f"{s['feature_failing_by_assertion']}/{s['feature_tests']} feature tests fail by "
                f"assertion; {s['regression_passing']}/{s['regression_tests']} regression pass")
        declared = s.get("feature_failing_by_declared_interface", 0)
        blocked = len(s.get("feature_files_blocked_by_declared_interface", ()))
        if declared or blocked:
            text += (f"; {declared} tests and {blocked} files fail on a declared missing name")
    elif world.name == "world2":
        text = (f"{s['feature_passing']}/{s['feature_tests']} feature tests pass; "
                f"{s['regression_passing']}/{s['regression_tests']} regression pass")
    elif world.name == "world3":
        text = (f"{s['killed']}/{s['mutants_executed']} mutants killed "
                f"({s['declared']} declared, {s['generated']} generated, {s['contract']} contract)"
                + (f"; {s['equivalent']} equivalent" if s["equivalent"] else "")
                + (f"; {s['invalid']} without verdict" if s["invalid"] else ""))
    else:
        text = ""
    label = "0" if world.name == "gates" else world.name[-1]
    return f"  {label}  {world.title:<16}{world.status.upper():<9}{text}"


def _progress(kind, *args):
    if kind == "run":
        print(f"run {args[0]}/{args[1]}")
    elif kind == "world":
        print(_world_line(args[0]), flush=True)


def _verify(args):
    package = Path(args.package)
    if not package.is_dir():
        raise HarnessError(f"package directory not found: {package}")
    out_dir = Path(args.out) if args.out else Path("unwritten-evidence") / package.resolve().name
    runner = make_runner(args.runner)
    print(f"UNWRITTEN verify  {package}")
    print(f"repository        {args.repo}")
    print(f"runner            {args.runner}"
          + ("   (NO ISOLATION: trusted repositories only)" if args.runner == "local" else ""))
    evidence = verify_package(package, args.repo, runner, out_dir, args.repeat, _progress,
                              max(1, args.jobs))

    repeatability = evidence.get("repeatability")
    if repeatability:
        state = "equivalent" if repeatability["equivalent"] else "DIFFERENT"
        print(f"repeatability     {repeatability['runs']} runs, {state} "
              f"(fingerprint {repeatability['fingerprints'][0][:12]})")
    print(f"\nVERDICT           {evidence['verdict'].upper()}")
    for reason in evidence["reasons"]:
        print(f"\n  {reason['code']}  [{reason['world']}]\n    {reason['message']}")
        for item in reason["items"][:_SHOWN_ITEMS]:
            print(f"      - {item}")
        if len(reason["items"]) > _SHOWN_ITEMS:
            print(f"      ... and {len(reason['items']) - _SHOWN_ITEMS} more")
    print(f"\nevidence          {Path(evidence['evidence_dir']) / EVIDENCE_FILE}")
    return 0 if evidence["verdict"] == "accepted" else 1


def _extract_progress(kind, *args):
    if kind == "static":
        print(f"static pass       {args[0]} adapter: {args[1]} symbols, {args[2]} guard clauses, "
              f"{args[3]} faults to inject")
    elif kind == "baseline":
        print(f"baseline (2 runs) {args[0]} tests, {args[1]} pass in both runs, {args[2]} unstable")
    elif kind == "mutants":
        print(f"fault injection   {args[1]}/{args[0]} faults detected by at least one test")
    sys.stdout.flush()


def _contracts_extract(args):
    profile = load_profile(args.profile)
    out_dir = Path(args.out) if args.out else Path("unwritten-evidence") / f"contracts-{profile.repo_name}"
    print(f"UNWRITTEN contracts extract  {profile.repo_name} @ {profile.commit[:12]}")
    print(f"runner            {args.runner}"
          + ("   (NO ISOLATION: trusted repositories only)" if args.runner == "local" else ""))
    contract_map = extract_contracts(args.repo, profile, make_runner(args.runner), out_dir,
                                     args.hypotheses, max(1, args.jobs), _extract_progress)
    summary = contract_map["summary"]
    print(f"\ncontracts         {summary['contracts']}: {summary.get('verified', 0)} verified, "
          f"{summary.get('hypothesis', 0)} hypotheses, {summary.get('refuted', 0)} refuted")
    for status in ("verified", "hypothesis", "refuted"):
        print(f"\n  {status.upper()}")
        for contract in contract_map["contracts"]:
            if contract["status"] == status:
                validation = contract["validation"]
                detail = (f"{validation['probe_count']} probes" if status == "verified"
                          else validation["result"])
                print(f"    {contract['id']}\n        {contract['description']}  [{detail}]")
    print(f"\nfaults            {summary['faults_injected']} injected: {summary['faults_killed']} killed, "
          f"{summary['faults_survived']} survived, {summary['faults_equivalent']} equivalent, "
          f"{summary['faults_unexecutable']} unexecutable")
    print(f"fingerprint       {contract_map['fingerprint'][:12]}")
    print(f"contract map      {Path(contract_map['map_dir']) / MAP_FILE}")
    return 0


def _contracts_attach(args):
    document = attach_contracts(args.package, args.map)
    print(f"attached {len(document['contracts'])} verified contracts to {args.package}")
    for contract in document["contracts"]:
        print(f"  {contract['id']}  ({len(contract['probes'])} probes)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="unwritten", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser(
        "verify", help="run the Three-World Test on a task package",
        description="Run the Three-World Test on a task package and write its evidence.")
    verify.add_argument("package", help="task package directory")
    verify.add_argument("--repo", required=True,
                        help="git repository that contains the package's pinned commit")
    verify.add_argument("--runner", choices=("docker", "local"), default="docker",
                        help="docker (default, isolated) or local (no isolation)")
    verify.add_argument("--repeat", type=int, default=2,
                        help="full runs to compare for repeatability (default 2, minimum 2)")
    verify.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                        help="mutants to execute in parallel (default: up to 4)")
    verify.add_argument("--out", help="evidence directory (default unwritten-evidence/<package>)")
    contracts = commands.add_parser("contracts", help="discover and attach behavioural contracts")
    actions = contracts.add_subparsers(dest="action", required=True)
    extract = actions.add_parser(
        "extract", help="build the contract map of a repository at a pinned commit",
        description="Discover contracts and verify them by execution.")
    extract.add_argument("--repo", required=True, help="git repository to analyse")
    extract.add_argument("--profile", required=True,
                         help="repository profile: commit, Dockerfile, test paths")
    extract.add_argument("--hypotheses", help="JSON file of supplied hypotheses with probes")
    extract.add_argument("--runner", choices=("docker", "local"), default="docker")
    extract.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1))
    extract.add_argument("--out", help="output directory (default unwritten-evidence/contracts-<repo>)")
    attach = actions.add_parser(
        "attach", help="add the relevant verified contracts to a task package",
        description="Attach verified contracts to a task package as regression protection.")
    attach.add_argument("package", help="task package directory")
    attach.add_argument("--map", required=True, help="directory written by 'contracts extract'")
    args = parser.parse_args(argv)
    try:
        if args.command == "contracts":
            return _contracts_extract(args) if args.action == "extract" else _contracts_attach(args)
        return _verify(args)
    except HarnessError as exc:
        print(f"\nHARNESS ERROR     {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
