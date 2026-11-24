"""What a data source does for the tools, whatever is behind it (a REST API, a database...).

The tools speak in five operations (list/get users, list/get trainings, list participants)
and in our field names; a source translates both ways. Records keep every field the source
returns; only the fields named in the configuration are renamed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import ToolInputError
from ..tenants import Tenant


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    total: int | None  # None: the source doesn't say how many exist


class DataSource(Protocol):
    name: str

    @property
    def operations(self) -> frozenset[str]: ...

    async def list(
        self,
        tenant: Tenant,
        operation: str,
        filters: Mapping[str, Any],
        path_args: Mapping[str, Any],
        max_items: int,
    ) -> Page: ...

    async def get(self, tenant: Tenant, operation: str, path_args: Mapping[str, Any]) -> dict: ...


def unsupported(source: str, operation: str, filters: list[str]) -> ToolInputError:
    names = ", ".join(sorted(filters))
    return ToolInputError(
        f"This organization's data source ({source}) can't filter {operation.removeprefix('list_')} "
        f"by {names}. Retry without it."
    )


def to_ours(record: dict[str, Any], fields: Mapping[str, str], values: Mapping[str, Mapping[str, str]]) -> dict:
    """Rename the configured fields (theirs → ours) and translate configured values back."""
    out = dict(record)
    for ours, theirs in fields.items():
        if theirs in out and theirs != ours:
            out[ours] = out.pop(theirs)
    for field, mapping in values.items():
        back = {v: k for k, v in mapping.items()}
        if field in out and isinstance(out[field], str | int) and str(out[field]) in back:
            out[field] = back[str(out[field])]
    return out


def to_theirs(field: str, value: Any, values: Mapping[str, Mapping[str, str]]) -> Any:
    """A filter value in the source's vocabulary (e.g. our 'learner' is their 'student')."""
    return values.get(field, {}).get(str(value), value)


def dig(data: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data
