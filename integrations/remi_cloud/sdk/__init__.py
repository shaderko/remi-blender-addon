"""Shared Remi Cloud API and embedded-client SDK. Production admission is gated."""

from ._version import __version__
# Embedded applications can import the shared core and bundled transport without
# installing the CLI's httpx/keyring dependencies into their host interpreter.
from .embedded import CloudSession, CloudError
from .workflow import CloudRequest
from .errors import (
    APIError,
    AuthenticationError,
    CredentialStoreError,
    BillingRequiredError,
    CapabilityUnavailableError,
    IntegrityError,
    JobFailedError,
    JobTimeoutError,
    NetworkError,
    ProtocolError,
    RemiError,
    SubmissionError,
)
from .models import Capabilities, CloudFile, Job, JobBilling

__all__ = [
    "Remi", "RemiAuth", "LoginChallenge", "UserSession", "Capabilities", "CloudFile", "Job", "RemiError", "APIError",
    "CredentialStoreError",
    "JobBilling",
    "AuthenticationError", "BillingRequiredError", "CapabilityUnavailableError",
    "IntegrityError", "JobFailedError", "JobTimeoutError", "NetworkError",
    "ProtocolError", "SubmissionError",
    "CloudSession", "CloudError", "CloudRequest",
]


def __getattr__(name):
    if name == "Remi":
        from .client import Remi
        return Remi
    if name in ("RemiAuth", "LoginChallenge", "UserSession"):
        from . import auth
        return getattr(auth, name)
    raise AttributeError(name)
