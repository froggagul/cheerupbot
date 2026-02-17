from __future__ import annotations

import json
import importlib
import importlib.util
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable


class FilterSpecError(ValueError):
    """Raised when a filter specification is invalid."""


Predicate = Callable[[dict[str, Any]], bool]


def load_filter_spec(filter_file: str = "", filter_json: str = "") -> Any | None:
    if not filter_file and not filter_json:
        return None
    if filter_file and filter_json:
        raise FilterSpecError("Use either --filter-file or --filter-json, not both.")

    raw = filter_json
    if filter_file:
        try:
            raw = Path(filter_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise FilterSpecError(f"Failed to read filter file: {filter_file} ({exc})") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        source = filter_file or "--filter-json"
        raise FilterSpecError(f"Invalid JSON in {source}: {exc}") from exc


def build_filter_predicate(spec: Any | None) -> Predicate:
    if spec is None:
        return lambda _: True

    if isinstance(spec, list):
        nodes = [_build_node(item) for item in spec]
        return lambda row: all(node(row) for node in nodes)

    return _build_node(spec)


def load_plugin_predicates(plugin_specs: list[str]) -> list[Predicate]:
    predicates: list[Predicate] = []
    for plugin_spec in plugin_specs:
        predicates.append(_load_plugin_predicate(plugin_spec))
    return predicates


def _load_plugin_predicate(plugin_spec: str) -> Predicate:
    raw = plugin_spec.strip()
    if ":" not in raw:
        raise FilterSpecError(
            f"Invalid --filter-plugin '{plugin_spec}'. Use module:function or /path/to/file.py:function."
        )
    module_ref, func_name = raw.rsplit(":", 1)
    module_ref = module_ref.strip()
    func_name = func_name.strip()
    if not module_ref or not func_name:
        raise FilterSpecError(
            f"Invalid --filter-plugin '{plugin_spec}'. Use module:function or /path/to/file.py:function."
        )

    fn = _load_plugin_function(module_ref, func_name)
    if not callable(fn):
        raise FilterSpecError(f"Plugin target is not callable: {plugin_spec}")

    def predicate(row: dict[str, Any]) -> bool:
        result = fn(row)
        if not isinstance(result, bool):
            raise FilterSpecError(f"Plugin '{plugin_spec}' must return bool, got {type(result).__name__}.")
        return result

    return predicate


def _load_plugin_function(module_ref: str, func_name: str):
    if module_ref.endswith(".py") or "/" in module_ref:
        path = Path(module_ref).expanduser().resolve()
        if not path.exists():
            raise FilterSpecError(f"Plugin file not found: {path}")
        module_name = f"job_filter_plugin_{abs(hash(str(path)))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise FilterSpecError(f"Cannot load plugin file: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(module_ref)
        except ModuleNotFoundError as exc:
            raise FilterSpecError(f"Plugin module not found: {module_ref}") from exc
    try:
        return getattr(module, func_name)
    except AttributeError as exc:
        raise FilterSpecError(f"Plugin function '{func_name}' not found in '{module_ref}'.") from exc


def _build_node(spec: Any) -> Predicate:
    if not isinstance(spec, dict):
        raise FilterSpecError("Each filter node must be a JSON object.")

    logical_keys = [key for key in ("all", "any", "not") if key in spec]
    has_leaf = "field" in spec or "fields" in spec

    if logical_keys and has_leaf:
        raise FilterSpecError("A node cannot mix logical keys (all/any/not) with field rules.")
    if len(logical_keys) > 1:
        raise FilterSpecError("A node can use only one of: all, any, not.")

    if "all" in spec:
        children = spec["all"]
        if not isinstance(children, list) or not children:
            raise FilterSpecError("'all' must be a non-empty array.")
        predicates = [_build_node(child) for child in children]
        return lambda row: all(predicate(row) for predicate in predicates)

    if "any" in spec:
        children = spec["any"]
        if not isinstance(children, list) or not children:
            raise FilterSpecError("'any' must be a non-empty array.")
        predicates = [_build_node(child) for child in children]
        return lambda row: any(predicate(row) for predicate in predicates)

    if "not" in spec:
        child = _build_node(spec["not"])
        return lambda row: not child(row)

    if has_leaf:
        return _build_leaf(spec)

    raise FilterSpecError("Unknown filter node. Use all/any/not or field/fields + op.")


def _build_leaf(spec: dict[str, Any]) -> Predicate:
    fields = _parse_fields(spec)
    op = str(spec.get("op", "contains")).strip().lower()
    matcher = str(spec.get("match", "any")).strip().lower()
    if matcher not in {"any", "all"}:
        raise FilterSpecError("Leaf matcher must be 'any' or 'all'.")

    expected = spec.get("value")
    case_sensitive = bool(spec.get("case_sensitive", False))

    evaluator = _build_evaluator(op=op, expected=expected, case_sensitive=case_sensitive)

    def predicate(row: dict[str, Any]) -> bool:
        values = [_resolve_field(row, field) for field in fields]
        results = [evaluator(value) for value in values]
        return any(results) if matcher == "any" else all(results)

    return predicate


def _parse_fields(spec: dict[str, Any]) -> list[str]:
    if "fields" in spec:
        fields = spec["fields"]
        if not isinstance(fields, list) or not fields:
            raise FilterSpecError("'fields' must be a non-empty array of field names.")
        if not all(isinstance(field, str) and field.strip() for field in fields):
            raise FilterSpecError("'fields' must contain only non-empty strings.")
        return [field.strip() for field in fields]

    field = spec.get("field")
    if not isinstance(field, str) or not field.strip():
        raise FilterSpecError("Leaf rule requires a non-empty 'field' or 'fields'.")
    return [field.strip()]


def _build_evaluator(op: str, expected: Any, case_sensitive: bool) -> Callable[[Any], bool]:
    if op == "exists":
        expected_bool = _to_bool(expected, default=True)
        return lambda actual: _is_present(actual) is expected_bool

    if op in {"contains", "not_contains", "eq", "ne", "starts_with", "ends_with"}:
        if expected is None:
            raise FilterSpecError(f"'{op}' requires 'value'.")
        expected_text = _normalize_text(expected, case_sensitive=case_sensitive)

        def evaluate_text(actual: Any) -> bool:
            actual_text = _normalize_text(actual, case_sensitive=case_sensitive)
            if op == "contains":
                return expected_text in actual_text
            if op == "not_contains":
                return expected_text not in actual_text
            if op == "eq":
                return actual_text == expected_text
            if op == "ne":
                return actual_text != expected_text
            if op == "starts_with":
                return actual_text.startswith(expected_text)
            return actual_text.endswith(expected_text)

        return evaluate_text

    if op in {"in", "not_in"}:
        if not isinstance(expected, list) or not expected:
            raise FilterSpecError(f"'{op}' requires a non-empty array 'value'.")
        expected_set = {
            _normalize_text(value, case_sensitive=case_sensitive)
            for value in expected
        }
        return lambda actual: (
            _normalize_text(actual, case_sensitive=case_sensitive) in expected_set
        ) if op == "in" else (
            _normalize_text(actual, case_sensitive=case_sensitive) not in expected_set
        )

    if op in {"regex", "not_regex"}:
        if not isinstance(expected, str) or not expected.strip():
            raise FilterSpecError(f"'{op}' requires a non-empty string 'value'.")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(expected, flags)
        except re.error as exc:
            raise FilterSpecError(f"Invalid regex for op '{op}': {exc}") from exc
        return lambda actual: bool(pattern.search(_stringify(actual))) if op == "regex" else (
            not bool(pattern.search(_stringify(actual)))
        )

    if op in {"gt", "gte", "lt", "lte"}:
        target = _to_float(expected, op)

        def evaluate_number(actual: Any) -> bool:
            value = _try_float(actual)
            if value is None:
                return False
            if op == "gt":
                return value > target
            if op == "gte":
                return value >= target
            if op == "lt":
                return value < target
            return value <= target

        return evaluate_number

    if op in {"date_before", "date_on_or_before", "date_after", "date_on_or_after"}:
        if not isinstance(expected, str):
            raise FilterSpecError(f"'{op}' requires ISO date string 'value'.")
        target_date = _parse_iso_date(expected)
        if target_date is None:
            raise FilterSpecError(f"'{op}' requires ISO date string 'YYYY-MM-DD' in 'value'.")

        def evaluate_date(actual: Any) -> bool:
            actual_date = _parse_iso_date(_stringify(actual))
            if actual_date is None:
                return False
            if op == "date_before":
                return actual_date < target_date
            if op == "date_on_or_before":
                return actual_date <= target_date
            if op == "date_after":
                return actual_date > target_date
            return actual_date >= target_date

        return evaluate_date

    if op == "is_open":
        expected_bool = _to_bool(expected, default=True)
        return lambda actual: (_normalize_text(actual, case_sensitive=False) == "open") is expected_bool

    raise FilterSpecError(f"Unsupported operator: {op}")


def _resolve_field(row: dict[str, Any], field: str) -> Any:
    if field == "_text":
        tokens: list[str] = []
        for value in row.values():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                tokens.append(json.dumps(value, ensure_ascii=False))
            else:
                tokens.append(str(value))
        return " ".join(tokens)

    current: Any = row
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def _normalize_text(value: Any, case_sensitive: bool) -> str:
    text = _stringify(value).strip()
    return text if case_sensitive else text.lower()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _to_float(value: Any, op: str) -> float:
    parsed = _try_float(value)
    if parsed is None:
        raise FilterSpecError(f"'{op}' requires numeric 'value'.")
    return parsed


def _try_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        token = value.strip().replace(",", "")
        if not token:
            return None
        try:
            return float(token)
        except ValueError:
            return None
    return None


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "y"}:
            return True
        if token in {"false", "0", "no", "n"}:
            return False
    return bool(value)


def _parse_iso_date(value: str) -> date | None:
    token = value.strip()
    if not token:
        return None
    if len(token) >= 10:
        token = token[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None
