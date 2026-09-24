"""Content accounting (guide §2.3 principle 1, §3.3): the ledger check behind ``check`` / ``export``."""

from parserx.accounting.check import CheckResult, IllegalRef, check

__all__ = ["CheckResult", "IllegalRef", "check"]
