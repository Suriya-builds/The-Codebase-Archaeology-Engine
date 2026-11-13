"""Attach verified contracts to a task package as regression protection.

Selects the verified contracts that concern the files a task's reference
patch touches, copies supplied probes into ``tests/contracts/`` and writes
``contracts.json``. ``unwritten verify`` then holds the reference
implementation to every one of them.
"""
import json
import shutil
from pathlib import Path

from unwritten.contracts.extract import MAP_FILE, PROBE_DIR
from unwritten.errors import HarnessError
from unwritten.package import CONTRACTS_FILE, load_package
from unwritten.patches import touched_paths
from unwritten.workspace import PACKAGE_TESTS_DIR

_SUITES = {"repo_tests": "regression_repo", "probes": "regression_contracts"}


def attach_contracts(package_dir, map_path):
    map_path = Path(map_path)
    map_dir = map_path if map_path.is_dir() else map_path.parent
    try:
        contract_map = json.loads((map_dir / MAP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessError(f"cannot read contract map in {map_dir}: {exc}") from exc
    package = load_package(package_dir)
    if contract_map["repository"]["commit"] != package.commit:
        raise HarnessError(
            "the contract map was extracted at commit "
            f"{contract_map['repository']['commit']}, but the package pins {package.commit}")

    touched = set(touched_paths(package.reference_patch.read_text(encoding="utf-8")))
    target = package.root / "tests" / "contracts"
    if target.exists():
        shutil.rmtree(target)
    attached = []
    for contract in contract_map["contracts"]:
        files = {contract["source"]["file"]} | {r["file"] for r in contract["related"]}
        if contract["status"] != "verified" or not files & touched:
            continue
        probes = []
        for probe in contract["validation"]["probes"]:
            nodeid = probe["nodeid"]
            if probe["suite"] == "probes":
                name = nodeid.split("::")[0].rsplit("/", 1)[1]
                target.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(map_dir / "probes" / name, target / name)
                nodeid = nodeid.replace(PROBE_DIR + "/", f"{PACKAGE_TESTS_DIR}/contracts/", 1)
            probes.append({"suite": _SUITES[probe["suite"]], "nodeid": nodeid})
        entry = {key: contract[key] for key in
                 ("id", "description", "category", "origin", "evidence_type", "source")}
        entry["probes"] = probes
        if "guard" in contract:
            entry["guard"] = contract["guard"]
        attached.append(entry)

    document = {
        "contract_map_fingerprint": contract_map["fingerprint"],
        "commit": package.commit,
        "selection": "verified contracts whose source or related symbols lie in files the "
                     "reference patch touches",
        "touched_files": sorted(touched),
        "contracts": attached,
    }
    (package.root / CONTRACTS_FILE).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document
