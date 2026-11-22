from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from .errors import ProtocolError

OPERATIONS = ("generate_image", "generate", "reconstruct", "optimize", "pipeline")
STATUSES = ("queued", "running", "succeeded", "failed", "canceled")


def object_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("Expected a JSON object from Remi Cloud.")
    return value


def text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError("Missing or invalid text in the API response.")
    return value


def identifier(value: Any) -> str:
    value = text(value)
    try:
        if str(UUID(value)) != value.lower():
            raise ValueError
    except ValueError as error:
        raise ProtocolError("Invalid resource UUID.") from error
    return value


def integer(value: Any, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ProtocolError("Invalid integer in the API response.")
    return value


def digest(value: Any) -> str:
    value = text(value)
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ProtocolError("Missing or invalid SHA-256 in the API response.")
    return value


def identifiers(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProtocolError("Expected a list of resource IDs.")
    return tuple(identifier(item) for item in value)


@dataclass(frozen=True)
class Capabilities:
    enabled: bool
    operations: dict[str, bool]
    input_modes: dict[str, bool]
    image_generation: dict[str, Any] | None = None

    @classmethod
    def parse(cls, value: Any) -> Capabilities:
        value = object_value(value)
        if value.get("apiVersion") != "v1" or type(value.get("enabled")) is not bool:
            raise ProtocolError("Unsupported API version or invalid capabilities.")
        operations = object_value(value.get("operations"))
        modes = object_value(value.get("inputModes"))
        billing = object_value(value.get("billing"))
        if billing.get("currency") != "EUR" or billing.get("spendCapsRequired") is not True:
            raise ProtocolError("Unsupported billing currency or spend-cap contract.")
        for name in OPERATIONS:
            if type(operations.get(name)) is not bool:
                raise ProtocolError("Invalid operation capabilities.")
        for name in ("single", "batch", "multiview"):
            if type(modes.get(name)) is not bool:
                raise ProtocolError("Invalid input-mode capabilities.")
        image = value.get("imageGeneration")
        if image is not None:
            image = object_value(image)
            text(image.get("model"))
            if (type(image.get("maxReferenceImages")) is not int or not 1 <= image["maxReferenceImages"] <= 16
                    or type(image.get("maxReferenceBytes")) is not int or not 1 <= image["maxReferenceBytes"] <= 20 * 1024 * 1024
                    or type(image.get("editing")) is not bool):
                raise ProtocolError("Invalid image context capabilities.")
        return cls(value["enabled"], dict(operations), dict(modes), dict(image) if image else None)


@dataclass(frozen=True)
class CloudFile:
    id: str
    kind: str
    filename: str
    content_type: str
    bytes: int
    sha256: str
    created_at: str

    @classmethod
    def parse(cls, value: Any) -> CloudFile:
        value = object_value(value)
        kind = value.get("kind")
        if kind not in ("image", "mesh", "splat"):
            raise ProtocolError("Unknown file kind.")
        return cls(identifier(value.get("id")), kind, text(value.get("filename")),
                   text(value.get("contentType")), integer(value.get("bytes"), minimum=1),
                   digest(value.get("sha256")), text(value.get("createdAt")))


@dataclass(frozen=True)
class JobBilling:
    status: str
    currency: str
    max_cost_micros: str
    actual_cost_micros: str | None

    @classmethod
    def parse(cls, value: Any) -> JobBilling:
        value = object_value(value)
        status = value.get("status")
        cap, actual = value.get("maxCostMicros"), value.get("actualCostMicros")
        def money(amount: Any) -> bool:
            return (isinstance(amount, str) and len(amount) <= 19
                    and re.fullmatch(r"0|[1-9][0-9]*", amount) is not None
                    and int(amount) <= 9223372036854775807)
        if (status not in ("pending", "settled", "no_charge") or value.get("currency") != "EUR"
                or not money(cap) or int(cap) == 0 or "actualCostMicros" not in value
                or (status == "pending" and actual is not None)
                or (status != "pending" and (not money(actual) or int(actual) > int(cap)))
                or (status == "no_charge" and actual != "0")):
            raise ProtocolError("Invalid job billing evidence.")
        return cls(status, "EUR", cap, actual)


@dataclass(frozen=True)
class Job:
    id: str
    operation: str
    input_mode: str
    input_file_ids: tuple[str, ...]
    status: str
    stage: str
    progress: float
    output_file_ids: tuple[str, ...]
    error: dict[str, str] | None
    created_at: str
    updated_at: str
    billing: JobBilling | None = None

    @property
    def terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "canceled")

    def to_dict(self) -> dict[str, Any]:
        """Python/CLI representation uses snake_case; HTTP uses camelCase."""
        return asdict(self)

    @classmethod
    def parse(cls, value: Any) -> Job:
        value = object_value(value)
        if (value.get("operation") not in OPERATIONS or value.get("status") not in STATUSES
                or value.get("inputMode") not in ("single", "multiview")):
            raise ProtocolError("Unknown job operation, mode, or status.")
        progress = value.get("progress")
        if type(progress) not in (float, int) or not 0 <= progress <= 1:
            raise ProtocolError("Job progress must be between 0 and 1.")
        error = value.get("error")
        if error is not None:
            error = object_value(error)
            error = {"code": text(error.get("code")), "message": text(error.get("message"))}
        outputs = identifiers(value.get("outputFileIds"))
        if value["status"] == "succeeded" and not outputs:
            raise ProtocolError("A succeeded job must have output artifacts.")
        inputs = identifiers(value.get("inputFileIds"))
        if (value["operation"] == "generate_image" and (len(inputs) > 16 or value["inputMode"] != "single")) or (value["operation"] != "generate_image" and not inputs):
            raise ProtocolError("A job must identify its input files.")
        return cls(identifier(value.get("id")), value["operation"], value["inputMode"], inputs,
                   value["status"], text(value.get("stage")), float(progress), outputs, error,
                   text(value.get("createdAt")), text(value.get("updatedAt")),
                   JobBilling.parse(value["billing"]) if "billing" in value else None)
