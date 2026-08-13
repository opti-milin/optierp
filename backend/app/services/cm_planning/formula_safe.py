"""Sandboxed arithmetic formulas for CM allocation (no builtins / imports)."""

from __future__ import annotations

import ast
import operator
from typing import Any

_BINOPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def eval_formula(expression: str, env: dict[str, float]) -> float:
    """Evaluate a restricted arithmetic expression against ``env`` names."""
    tree = ast.parse(expression.strip(), mode="eval")
    return float(_eval_node(tree.body, env))


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"Unknown name '{node.id}' in formula")
        return float(env[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left = _eval_node(node.left, env)
        right = _eval_node(node.right, env)
        return float(_BINOPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return float(_UNARY[type(node.op)](_eval_node(node.operand, env)))
    raise ValueError(f"Disallowed expression node: {type(node).__name__}")
