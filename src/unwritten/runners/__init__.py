from unwritten.errors import HarnessError
from unwritten.runners.docker import DockerRunner
from unwritten.runners.local import LocalRunner


def make_runner(name):
    if name == "docker":
        return DockerRunner()
    if name == "local":
        return LocalRunner()
    raise HarnessError(f"unknown runner: {name}")
