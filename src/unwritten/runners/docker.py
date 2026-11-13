"""Runs suites in a throwaway Docker container.

The image is built from the package's Dockerfile and holds dependencies only.
Each run gets a fresh container with no network, a read-only root filesystem,
dropped capabilities, a non-root user and memory, CPU and process limits.

Image builds need network access. Behind a TLS-intercepting proxy, set:

    UNWRITTEN_BUILD_NETWORK=host        passed as ``docker build --network``
    UNWRITTEN_BUILD_CA_BUNDLE=<file>    offered to the build as secret ``ca_bundle``

Proxy variables (HTTP_PROXY, HTTPS_PROXY, NO_PROXY) are forwarded as build
arguments when set. None of this reaches the running containers.
"""
import hashlib
import os
import subprocess
import uuid
from pathlib import Path

from unwritten.errors import HarnessError
from unwritten.runners.base import INNER_DIR, read_summary, write_spec

LIMITS = {"memory": "1g", "cpus": "2", "pids": "256", "tmpfs": "/tmp:rw,size=256m"}
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")


def _docker(*args, timeout=None):
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise HarnessError("docker is not installed") from exc


class DockerRunner:
    name = "docker"

    def __init__(self):
        self.image = None

    def prepare(self, package):
        if _docker("version", "--format", "{{.Server.Version}}", timeout=30).returncode != 0:
            raise HarnessError(
                "the Docker daemon is not reachable. Start it, or pass --runner local "
                "for a repository you trust.")
        content = package.dockerfile.read_bytes()
        self.image = "unwritten-env:" + hashlib.sha256(content).hexdigest()[:16]
        if _docker("image", "inspect", self.image).returncode != 0:
            self._build(package.dockerfile)
        image_id = _docker("image", "inspect", "--format", "{{.Id}}", self.image).stdout.strip()
        return {
            "runner": "docker",
            "isolation": {"network": "none", "read_only_root": True, "capabilities": "none",
                          "user": self._user(), **LIMITS},
            "image": self.image,
            "image_id": image_id,
            "dockerfile_sha256": hashlib.sha256(content).hexdigest(),
        }

    def _build(self, dockerfile):
        command = ["build", "--quiet", "--tag", self.image, "--file", str(dockerfile)]
        if os.environ.get("UNWRITTEN_BUILD_NETWORK"):
            command += ["--network", os.environ["UNWRITTEN_BUILD_NETWORK"]]
        if os.environ.get("UNWRITTEN_BUILD_CA_BUNDLE"):
            command += ["--secret", "id=ca_bundle,src=" + os.environ["UNWRITTEN_BUILD_CA_BUNDLE"]]
        for name in _PROXY_VARS:
            if os.environ.get(name):
                command += ["--build-arg", name]
        result = _docker(*command, str(dockerfile.parent), timeout=1800)
        if result.returncode != 0:
            raise HarnessError(f"docker build failed:\n{result.stderr[-3000:]}")

    @staticmethod
    def _user():
        # Never run the repository's code as root, even inside the container.
        return "65534:65534" if os.getuid() == 0 else f"{os.getuid()}:{os.getgid()}"

    def run(self, workspace, suites, package, out_dir):
        write_spec(out_dir, suites, package.python_path, package.timeout_seconds)
        workspace, out_dir = Path(workspace).resolve(), Path(out_dir).resolve()
        if os.getuid() == 0:
            for root in (workspace, out_dir):
                for path in [root, *root.rglob("*")]:
                    path.chmod(0o777 if path.is_dir() else 0o666)
        name = "unwritten-" + uuid.uuid4().hex[:12]
        budget = package.timeout_seconds * len(suites) + 60
        command = [
            "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--tmpfs", LIMITS["tmpfs"],
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", LIMITS["memory"], "--memory-swap", LIMITS["memory"],
            "--cpus", LIMITS["cpus"], "--pids-limit", LIMITS["pids"],
            "--user", self._user(), "--env", "HOME=/tmp",
            "--volume", f"{workspace}:/workspace:rw",
            "--volume", f"{INNER_DIR}:/unwritten-inner:ro",
            "--volume", f"{out_dir}:/unwritten-out:rw",
            "--workdir", "/workspace", self.image,
            "python", "/unwritten-inner/run_suites.py", "/unwritten-out/spec.json", "/unwritten-out",
        ]
        try:
            result = _docker(*command, timeout=budget)
        except subprocess.TimeoutExpired as exc:
            _docker("kill", name, timeout=30)
            raise HarnessError(f"container exceeded {budget}s and was killed") from exc
        return read_summary(out_dir, result.stdout + result.stderr)
