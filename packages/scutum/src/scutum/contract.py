"""Data contract models and vectorized Arrow validation."""

from dataclasses import dataclass

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field


class ContractColumn(BaseModel):
    """Expected field type and nullability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str = Field(description="Arrow type string, for example int64 or string")
    nullable: bool = True


@dataclass(frozen=True)
class ContractViolation:
    field: str
    message: str


@dataclass(frozen=True)
class ContractReport:
    contract: str
    ok: bool
    violations: tuple[ContractViolation, ...]


class DataContract(BaseModel):
    """A strict or additive schema contract for a RecordBatch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    columns: list[ContractColumn] = Field(min_length=1)
    allow_extra: bool = False

    def check(self, batch: pa.RecordBatch) -> ContractReport:
        actual = {field.name: field for field in batch.schema}
        expected = {column.name: column for column in self.columns}
        violations: list[ContractViolation] = []
        for column in self.columns:
            field = actual.get(column.name)
            if field is None:
                violations.append(ContractViolation(column.name, "missing column"))
                continue
            if str(field.type) != column.type:
                violations.append(
                    ContractViolation(
                        column.name,
                        f"type mismatch: expected {column.type}, got {field.type}",
                    )
                )
            if not column.nullable and batch.column(column.name).null_count > 0:
                violations.append(ContractViolation(column.name, "contains null values"))
        if not self.allow_extra:
            for name in actual:
                if name not in expected:
                    violations.append(ContractViolation(name, "unexpected column"))
        return ContractReport(self.name, not violations, tuple(violations))
