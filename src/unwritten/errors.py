class HarnessError(Exception):
    """The harness itself could not do its job (infrastructure, bad arguments).

    Never reported as a rejection: a task is only rejected on evidence.
    """


class PackageError(Exception):
    """The task package is malformed. Carries every problem found."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))
