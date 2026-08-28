from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

type Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", min_length=1, max_length=128),
]
type JsonPointer = Annotated[str, StringConstraints(pattern=r"^(?:/[^/~]*(?:~[01][^/~]*)*)*$", max_length=512)]
type ScalarValue = str | int | float | bool | None
type AttributeValue = ScalarValue | tuple[ScalarValue, ...]


class ContractModel(BaseModel):
    """Strict base class for objects that cross a process or persistence boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TimestampedContractModel(ContractModel):
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value
