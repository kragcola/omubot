#!/usr/bin/env python3
"""Generate the small TypeScript boundary from the Pydantic web DTO schemas."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".cache" / "web-contracts.ts"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_ALLOWED_KEYS = {
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "default",
    "discriminator",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "oneOf",
    "pattern",
    "prefixItems",
    "properties",
    "propertyNames",
    "required",
    "title",
    "type",
}


class SchemaExportError(ValueError):
    """Raised when a DTO schema uses a construct this focused generator cannot render."""


Schema = dict[str, object]


def _object(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchemaExportError(f"{context} must be a JSON object")
    return value


def _schema(value: object, context: str) -> Schema:
    return dict(_object(value, context))


def _same_schema_body(left: Schema, right: Schema) -> bool:
    """A root schema may repeat its referenced definitions under ``$defs``."""
    return (
        {key: value for key, value in left.items() if key != "$defs"}
        == {key: value for key, value in right.items() if key != "$defs"}
    )


def _schemas_from_models(models: Sequence[type[BaseModel]]) -> dict[str, Schema]:
    collected: dict[str, Schema] = {}
    for model in models:
        name = model.__name__
        schema = _schema(model.model_json_schema(), name)
        prior = collected.get(name)
        if prior is not None and not _same_schema_body(prior, schema):
            raise SchemaExportError(f"conflicting schema for {name}")
        collected[name] = schema
        definitions = _object(schema.get("$defs", {}), f"{name}.$defs")
        for definition_name, definition in definitions.items():
            definition_schema = _schema(definition, f"{name}.$defs.{definition_name}")
            prior = collected.get(definition_name)
            if prior is not None and not _same_schema_body(prior, definition_schema):
                raise SchemaExportError(f"conflicting schema for {definition_name}")
            if prior is None:
                collected[definition_name] = definition_schema
    return collected


def _literal(value: object, context: str) -> str:
    if isinstance(value, (dict, list, tuple)):
        raise SchemaExportError(f"unsupported non-scalar enum value at {context}")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise SchemaExportError(f"unsupported enum value at {context}")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SchemaExportError(f"unsupported enum value at {context}") from exc
    if encoded is None:
        raise SchemaExportError(f"unsupported enum value at {context}")
    return encoded


class _Emitter:
    def __init__(self) -> None:
        self.uses_json_value = False

    def type_expr(self, value: object, context: str) -> str:
        schema = _schema(value, context)
        unknown = sorted(set(schema) - _ALLOWED_KEYS)
        if unknown:
            raise SchemaExportError(f"unsupported schema keyword(s) at {context}: {', '.join(unknown)}")

        reference = schema.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise SchemaExportError(f"unsupported schema reference at {context}")
            name = reference.removeprefix("#/$defs/")
            if name == "JsonValue":
                self.uses_json_value = True
            return name

        if "const" in schema:
            return _literal(schema["const"], context)

        enum = schema.get("enum")
        enum_expr: str | None = None
        if enum is not None:
            if not isinstance(enum, list) or not enum:
                raise SchemaExportError(f"enum must be a non-empty list at {context}")
            enum_expr = " | ".join(_literal(item, context) for item in enum)

        alternative_keys = [key for key in ("anyOf", "oneOf") if key in schema]
        if len(alternative_keys) > 1:
            raise SchemaExportError(f"anyOf and oneOf cannot be combined at {context}")
        alternative_key = alternative_keys[0] if alternative_keys else None
        if "discriminator" in schema:
            if alternative_key != "oneOf":
                raise SchemaExportError(f"discriminator is only supported with oneOf at {context}")
            _object(schema["discriminator"], f"{context}.discriminator")

        if alternative_key is not None:
            alternatives = schema[alternative_key]
            if not isinstance(alternatives, list) or not alternatives:
                raise SchemaExportError(f"{alternative_key} must be a non-empty list at {context}")
            expressions: list[str] = []
            if enum_expr is not None:
                expressions.append(enum_expr)
            expressions.extend(
                self.type_expr(item, f"{context}.{alternative_key}[{index}]")
                for index, item in enumerate(alternatives)
            )
            unique = list(dict.fromkeys(expressions))
            return " | ".join(unique)

        if enum_expr is not None:
            return enum_expr

        schema_type = schema.get("type")
        if schema_type == "null":
            return "null"
        if schema_type == "string":
            return "string"
        if schema_type == "integer" or schema_type == "number":
            return "number"
        if schema_type == "boolean":
            return "boolean"
        if schema_type == "array":
            prefix = schema.get("prefixItems")
            if prefix is not None:
                if not isinstance(prefix, list):
                    raise SchemaExportError(f"only fixed-length tuples are supported at {context}")
                tuple_items = cast(list[object], prefix)
                if ("items" in schema or schema.get("minItems") != len(tuple_items)
                        or schema.get("maxItems") != len(tuple_items)):
                    raise SchemaExportError(f"only fixed-length tuples are supported at {context}")
                return "[" + ", ".join(
                    self.type_expr(item, f"{context}.prefixItems[{index}]")
                    for index, item in enumerate(tuple_items)
                ) + "]"
            items = schema.get("items")
            if items is None:
                raise SchemaExportError(f"array items missing at {context}")
            return f"Array<{self.type_expr(items, f'{context}.items')}>"
        if schema_type == "object":
            properties = _object(schema.get("properties", {}), f"{context}.properties")
            additional = schema.get("additionalProperties", False)
            if additional is True:
                raise SchemaExportError(f"unbounded additionalProperties at {context}")
            if isinstance(additional, Mapping):
                value_type = self.type_expr(additional, f"{context}.additionalProperties")
                property_names = schema.get("propertyNames")
                if property_names is not None:
                    key_type = self.type_expr(property_names, f"{context}.propertyNames")
                    return f"Partial<Record<{key_type}, {value_type}>>"
                if not properties:
                    return f"Record<string, {value_type}>"
            elif additional is not False and additional is not None:
                raise SchemaExportError(f"additionalProperties must be a schema or false at {context}")
            if not properties:
                self.uses_json_value = True
                return "JsonValue"
            required = schema.get("required", [])
            if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
                raise SchemaExportError(f"required must be a string list at {context}")
            required_names = set(cast(list[str], required))
            fields: list[str] = []
            for name in sorted(properties):
                if not isinstance(name, str):
                    raise SchemaExportError(f"property name must be a string at {context}")
                marker = "" if name in required_names else "?"
                property_name = name if _IDENTIFIER.fullmatch(name) else json.dumps(name, ensure_ascii=False)
                fields.append(
                    f"  {property_name}{marker}: "
                    f"{self.type_expr(properties[name], f'{context}.properties.{name}')};"
                )
            return "{\n" + "\n".join(fields) + "\n}"
        if schema_type is None and not schema:
            self.uses_json_value = True
            return "JsonValue"
        raise SchemaExportError(f"unsupported schema type at {context}: {schema_type!r}")

    def declaration(self, name: str, schema: Schema) -> str:
        type_expr = self.type_expr(schema, name)
        if schema.get("type") == "object" and schema.get("properties"):
            properties = _object(schema["properties"], f"{name}.properties")
            required = schema.get("required", [])
            if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
                raise SchemaExportError(f"required must be a string list at {name}")
            required_names = set(cast(list[str], required))
            fields = []
            for property_name in sorted(properties):
                marker = "" if property_name in required_names else "?"
                rendered_name = (
                    property_name
                    if _IDENTIFIER.fullmatch(property_name)
                    else json.dumps(property_name, ensure_ascii=False)
                )
                fields.append(
                    f"  {rendered_name}{marker}: "
                    f"{self.type_expr(properties[property_name], f'{name}.{property_name}')};"
                )
            return f"export interface {name} {{\n" + "\n".join(fields) + "\n}"
        return f"export type {name} = {type_expr};"


def render_schemas(schemas: Mapping[str, Mapping[str, object]]) -> str:
    """Render named root/definition schemas; unsupported JSON Schema fails explicitly."""
    merged: dict[str, Schema] = {name: dict(schema) for name, schema in schemas.items()}
    for root_name, root in list(merged.items()):
        definitions = _object(root.get("$defs", {}), f"{root_name}.$defs")
        for definition_name, definition in definitions.items():
            definition_schema = _schema(definition, f"{root_name}.$defs.{definition_name}")
            prior = merged.get(definition_name)
            if prior is not None and not _same_schema_body(prior, definition_schema):
                raise SchemaExportError(f"conflicting schema for {definition_name}")
            if prior is None:
                merged[definition_name] = definition_schema

    # Pydantic's JsonValue contributes an empty self-referential definition.
    # The emitter renders that recursive type below, so treating the empty
    # definition as a second named DTO would emit `type JsonValue = JsonValue`.
    if "JsonValue" in merged:
        if merged["JsonValue"] != {}:
            raise SchemaExportError("unexpected JsonValue definition")
        del merged["JsonValue"]

    emitter = _Emitter()
    declarations = [emitter.declaration(name, merged[name]) for name in sorted(merged)]
    header = "/* Generated by scripts/dev/export-web-contracts.py; do not edit. */"
    if emitter.uses_json_value:
        json_value = (
            "export type JsonValue = null | boolean | number | string | JsonValue[] | "
            "{ [key: string]: JsonValue };"
        )
        declarations.insert(0, json_value)
    return header + "\n\n" + "\n\n".join(declarations) + "\n"


def render_contracts() -> str:
    source = str(ROOT / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from omubot_new.web_contracts import WEB_CONTRACTS

    schemas = _schemas_from_models(WEB_CONTRACTS)
    return render_schemas(schemas)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the selected output is missing or differs from the generated contract",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    try:
        generated = render_contracts()
        if args.check:
            if not output.is_file():
                raise SchemaExportError(f"contract output is missing: {output}")
            if output.read_text(encoding="utf-8") != generated:
                raise SchemaExportError(f"contract output is out of date: {output}")
            print(f"PASS: web contract is current ({output})")
            return 0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(generated, encoding="utf-8")
        print(f"WROTE: web contract ({output})")
        return 0
    except (OSError, SchemaExportError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
