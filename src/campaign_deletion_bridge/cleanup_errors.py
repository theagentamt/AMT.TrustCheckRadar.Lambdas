"""Fixed campaign cleanup diagnostic; importable without AWS SDK initialization."""


class CoverageUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('Campaign deletion coverage is not verified')
