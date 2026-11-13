"""Command line interface.

    unwritten verify <package> --repo <git repository> [--runner docker|local]

Exit status: 0 accepted, 1 rejected, 2 the harness could not complete.
"""
import argparse
import os
import sys
from pathlib import Path

from unwritten.errors import HarnessError
from unwritten.runners import make_runner
from unwritten.verify import EVIDENCE_FILE, verify_package

_SHOWN_ITEMS = 8


def _world_line(world):
    s = world.summary
    if world.status == "not_run" or "feature_tests" not in s and "mutants_executed" not in s:
        text = s.get("note", "")  # skipped, or stopped before anything ran
    elif world.name == "world1":
        text = (f"{s['feature_failing_by_assertion']}/{s['feature_tests']} feature tests fail by "
                f"assertion; {s['regression_passing']}/{s['regression_tests']} regression pass")
    elif world.name == "world2":
        text = (f"{s['feature_passing']}/{s['feature_tests']} feature tests pass; "
                f"{s['regression_passing']}/{s['regression_tests']} regression pass")
    elif world.name == "world3":
        text = (f"{s['killed']}/{s['mutants_executed']} mutants killed "
                f"({s['declared']} declared, {s['generated']} generated)")
    else:
        text = ""
    return f"  {world.name[-1]}  {world.title:<16}{world.status.upper():<9}{text}"


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
    args = parser.parse_args(argv)
    try:
        return _verify(args)
    except HarnessError as exc:
        print(f"\nHARNESS ERROR     {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
