"""Composition-root contract for homophone prompt-provider wiring."""

from __future__ import annotations

import ast
import inspect
import textwrap

from bootstrap.chat_runtime import build_chat_runtime
from services.block_trace.homophone_provider import HomophoneProvider


def _is_runtime_slang_store_getter(node: ast.expr) -> bool:
    if not isinstance(node, ast.Lambda):
        return False
    if node.args.posonlyargs or node.args.args or node.args.kwonlyargs or node.args.vararg or node.args.kwarg:
        return False

    body = node.body
    if (
        isinstance(body, ast.Attribute)
        and isinstance(body.value, ast.Name)
        and body.value.id == "ctx"
        and body.attr == "slang_store"
    ):
        return True
    return (
        isinstance(body, ast.Call)
        and isinstance(body.func, ast.Name)
        and body.func.id == "getattr"
        and 2 <= len(body.args) <= 3
        and isinstance(body.args[0], ast.Name)
        and body.args[0].id == "ctx"
        and isinstance(body.args[1], ast.Constant)
        and body.args[1].value == "slang_store"
        and not body.keywords
    )


def test_build_chat_runtime_registers_homophone_provider_on_llm_bus() -> None:
    assert HomophoneProvider().name == "homophone"

    tree = ast.parse(textwrap.dedent(inspect.getsource(build_chat_runtime)))
    function = tree.body[0]
    assert isinstance(function, ast.AsyncFunctionDef)

    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(function)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "HomophoneProvider"
    }
    assert imported_names, "build_chat_runtime must import HomophoneProvider"

    provider_bus_names = {
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "PromptProviderBus"
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert provider_bus_names, "build_chat_runtime must construct a PromptProviderBus"

    register_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in provider_bus_names
        and node.func.attr == "register"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id in imported_names
    ]
    assert len(register_calls) == 1, (
        "build_chat_runtime must register exactly one HomophoneProvider on "
        "the PromptProviderBus"
    )

    provider_call = register_calls[0].args[0]
    assert isinstance(provider_call, ast.Call)
    assert not provider_call.args, "HomophoneProvider dependencies must use explicit keywords"
    slang_getters = [
        keyword.value
        for keyword in provider_call.keywords
        if keyword.arg == "slang_store_getter"
    ]
    assert len(slang_getters) == 1, (
        "HomophoneProvider must receive exactly one slang_store_getter from the composition root"
    )
    assert len(provider_call.keywords) == 1, (
        "HomophoneProvider wiring should expose only the required slang-store dependency"
    )
    assert _is_runtime_slang_store_getter(slang_getters[0]), (
        "slang_store_getter must be a zero-argument getter for the current ctx.slang_store"
    )

    parents = {
        child: parent
        for parent in ast.walk(function)
        for child in ast.iter_child_nodes(parent)
    }
    register_statement = parents[register_calls[0]]
    assert isinstance(register_statement, ast.Expr)
    assert parents[register_statement] is function, (
        "HomophoneProvider registration must be unconditional"
    )

    llm_bus_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "llm"
        and node.func.attr == "set_provider_bus"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id in provider_bus_names
    ]
    assert len(llm_bus_calls) == 1, (
        "build_chat_runtime must give the populated PromptProviderBus to the LLM"
    )
    assert register_calls[0].lineno < llm_bus_calls[0].lineno
