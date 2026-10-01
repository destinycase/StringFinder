"""JSON decoding with explicit duplicate-key policy and stack-safe fallback."""
import json
import math
from typing import Any, NoReturn


class ObjectPairs(list):
    """Ordered object members, distinct from JSON arrays; retain duplicate values."""


class DuplicateKeyError(ValueError):
    pass


def _reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON number out of range")
    return result


def loads_document(content: str, allow_duplicates: bool = False):
    def object_pairs(pairs):
        if allow_duplicates:
            return ObjectPairs(pairs)
        members = dict(pairs)
        # Keys are already decoded strings. A shorter dict proves duplicates
        # without a second Python loop and an additional set per object.
        if len(members) != len(pairs):
            raise DuplicateKeyError("duplicate JSON object key")
        return members

    try:
        return json.loads(content, object_pairs_hook=object_pairs, parse_constant=_reject_constant, parse_float=_finite_float)
    except RecursionError:
        return _loads_iterative(content, object_pairs)


def _loads_iterative(content, object_pairs):
    """Validate the same JSON grammar without relying on Python's call stack."""
    decoder = json.JSONDecoder(parse_constant=_reject_constant, parse_float=_finite_float)
    frames: list[list[Any]] = []
    root: list[Any] = []
    index = 0

    def error() -> NoReturn:
        raise json.JSONDecodeError("Invalid JSON document", content, index)

    def attach(value):
        if not frames:
            if root:
                error()
            root.append(value)
            return
        frame = frames[-1]
        if frame[0] == "object":
            if frame[2] != "value":
                error()
            frame[1].append((frame[3], value))
        else:
            if frame[2] not in ("first", "value"):
                error()
            frame[1].append(value)
        frame[2] = "separator"

    while index < len(content):
        char = content[index]
        if char in " \t\r\n":
            index += 1
            continue
        frame = frames[-1] if frames else None
        if char in "}]":
            if not frame or char != ("}" if frame[0] == "object" else "]"):
                error()
            if frame[2] not in ("first", "separator"):
                error()
            frames.pop()
            attach(object_pairs(frame[1]) if frame[0] == "object" else frame[1])
            index += 1
            continue
        if frame and frame[2] == "separator":
            if char != ",":
                error()
            frame[2] = "key" if frame[0] == "object" else "value"
            index += 1
            continue
        if frame and frame[0] == "object" and frame[2] in ("first", "key"):
            if char != '"':
                error()
            frame[3], index = decoder.raw_decode(content, index)
            frame[2] = "colon"
            continue
        if frame and frame[2] == "colon":
            if char != ":":
                error()
            frame[2] = "value"
            index += 1
            continue
        if frame and frame[2] not in ("first", "value"):
            error()
        if not frame and root:
            error()
        if char in "{[":
            frames.append(["object" if char == "{" else "array", [], "first", None])
            index += 1
        else:
            value, index = decoder.raw_decode(content, index)
            attach(value)
    if frames or not root:
        error()
    return root[0]
