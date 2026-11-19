from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Job


class RemiError(Exception):
    """Base exception. Error strings never include API keys or signed URLs."""


class APIError(RemiError):
    def __init__(self, message: str, *, code: str, status: int, request_id: str | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.retryable = retryable


class AuthenticationError(APIError):
    pass


class CredentialStoreError(AuthenticationError):
    def __init__(self, message: str):
        super().__init__(message, code="credential_store_unavailable", status=401)


class BillingRequiredError(APIError):
    pass


class CapabilityUnavailableError(APIError):
    pass


class NetworkError(RemiError):
    """An ambiguous transport failure. Resubmit only with the SAME idempotency key."""


class ProtocolError(RemiError):
    pass


class IntegrityError(RemiError):
    pass


class JobFailedError(RemiError):
    def __init__(self, job: Job):
        super().__init__(f"Job {job.id} ended with status {job.status}.")
        self.job = job


class JobTimeoutError(RemiError):
    def __init__(self, job_id: str):
        super().__init__(f"Timed out waiting for job {job_id}; it was NOT canceled. Resume waiting by ID.")
        self.job_id = job_id


class SubmissionError(RemiError):
    def __init__(self, cause: Exception, jobs: list[Job], idempotency_key: str):
        super().__init__("Submission stopped. Some jobs may already exist; resume using the SAME idempotency key.")
        self.cause = cause
        self.jobs = tuple(jobs)
        self.idempotency_key = idempotency_key
