import csv
import json
from dataclasses import asdict, is_dataclass, fields
from typing import Any, Type, TypeVar, get_origin, get_args
from pathlib import Path
from datetime import datetime, date, timezone

MAX_FIELD_SIZE = 10 * 1024 * 1024   # 10 MB
csv.field_size_limit(MAX_FIELD_SIZE)

T = TypeVar("T")

# ---------- Helpers ----------
def serialize_value(value: Any) -> Any:
    """Serialize dataclasses and complex objects to JSON, primitives as-is."""
    if isinstance(value, (datetime, date)):
        return value.astimezone(timezone.utc).isoformat()  # always +00:00
    elif is_dataclass(value):
        return json.dumps(asdict(value))
    elif isinstance(value, list):
        return json.dumps([serialize_value(v) for v in value])
    else:
        return value

def deserialize_value(value: Any, target_type: Any) -> Any:
    """Deserialize JSON strings back into dataclasses/lists if needed."""
    if value is None or value == "":
        if target_type == str:
            return ""
        return None

    origin = get_origin(target_type)

    # Handle datetime types
    if target_type == datetime:
        # check github's datetime format that is not exactly what datetime wants ...
        if value [-1] == 'Z':
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(value)
    elif target_type == date:
        return date.fromisoformat(value)
    elif target_type == bool:
        # bool("False") is True in Python - any non-empty string is truthy -
        # so this needs its own branch rather than falling into target_type(value).
        return str(value).strip().lower() in ("true", "1", "yes")
    elif is_dataclass(target_type):
        return target_type(**json.loads(value))
    elif origin == list:
        inner_type = get_args(target_type)[0]
        return [deserialize_value(v, inner_type) for v in json.loads(value)]
    else:
        # Convert to primitive type if needed
        try:
            return target_type(value)
        except Exception:
            return value


def dataclass_to_row(obj: Any) -> dict:
    """Flatten dataclass into a row dict (for CSV)."""
    row = {}
    for f in fields(obj):
        row[f.name] = serialize_value(getattr(obj, f.name))
    return row


def row_to_dataclass(row: dict, cls: Type[T]) -> T:
    """Rebuild dataclass from a row dict (from CSV)."""
    kwargs = {}
    for f in fields(cls):
        value = row.get(f.name)
        kwargs[f.name] = deserialize_value(value, f.type)
    return cls(**kwargs)


# ---------- Main API ----------

def write_dataclasses_to_csv(filename: Path, objects: list[Any], cls: type[T]):
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[f.name for f in fields(cls)])
        writer.writeheader()
        for obj in objects:
            writer.writerow(dataclass_to_row(obj))


def read_dataclasses_from_csv(filename: Path, cls: type[T]) -> list[T]:
    with open(filename, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row_to_dataclass(row, cls) for row in reader]
