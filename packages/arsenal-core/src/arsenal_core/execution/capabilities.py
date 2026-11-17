"""Capability declarations used to reject unsafe execution before submission."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuaranteeRequest:
    result_exactly_once: bool = False
    atomic_publish: bool = False
    delete_apply: bool = False
    watermark: bool = False


@dataclass(frozen=True)
class ExecutorCapabilities:
    result_exactly_once: bool = False
    atomic_publish: bool = False
    delete_apply: bool = False
    watermark: bool = False
    concurrent_writers: bool = False
    input_versioning: bool = False

    def supports(self, request: GuaranteeRequest) -> bool:
        return not any(
            (
                request.result_exactly_once and not self.result_exactly_once,
                request.atomic_publish and not self.atomic_publish,
                request.delete_apply and not self.delete_apply,
                request.watermark and not self.watermark,
            )
        )

    def missing(self, request: GuaranteeRequest) -> tuple[str, ...]:
        fields = ("result_exactly_once", "atomic_publish", "delete_apply", "watermark")
        return tuple(
            field for field in fields if getattr(request, field) and not getattr(self, field)
        )


@dataclass(frozen=True)
class SinkCapabilities:
    idempotent_batch_write: bool = False
    atomic_publish: bool = False
    delete_apply: bool = False
    concurrent_writers: bool = False
    input_versioning: bool = False

    def supports(self, request: GuaranteeRequest) -> bool:
        if request.result_exactly_once and not (
            self.idempotent_batch_write and self.atomic_publish
        ):
            return False
        return not any(
            (
                request.atomic_publish and not self.atomic_publish,
                request.delete_apply and not self.delete_apply,
            )
        )
