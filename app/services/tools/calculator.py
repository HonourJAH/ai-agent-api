import ast
import operator

# Only these AST node types are ever permitted.

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Caps how large a base or exponent can be in a Pow node.
_MAX_POW_OPERAND = 1_000_000


class CalculatorError(ValueError):
    """Raised for any expression that can't be safely evaluated —
    invalid syntax, a disallowed construct, or a guarded-against
    resource risk (e.g. an enormous exponent).
    """


# This is the recursive evaluation function. It walks the AST and evaluates
# each node, returning the final result. It raises CalculatorError for any
# disallowed node type or other error condition.
def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalculatorError(f"Unsupported constant: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise CalculatorError(f"Unsupported operator: {type(node.op).__name__}")

        left = _eval_node(node.left)
        right = _eval_node(node.right)

        if isinstance(node.op, ast.Pow) and (
            abs(left) > _MAX_POW_OPERAND or abs(right) > _MAX_POW_OPERAND
        ):
            raise CalculatorError("Exponent or base too large to evaluate safely")

        try:
            return op_func(left, right)
        except ZeroDivisionError:
            raise CalculatorError("Division by zero")

    if isinstance(node, ast.UnaryOp):
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise CalculatorError(
                f"Unsupported unary operator: {type(node.op).__name__}"
            )
        return op_func(_eval_node(node.operand))

    raise CalculatorError(f"Unsupported expression element: {type(node).__name__}")


def calculate(expression: str) -> float:
    """Safely evaluate a basic arithmetic expression.

    Supports +, -, *, /, //, %, ** and parentheses, on plain numbers only.
    Never evaluates variables, function calls, attribute access, or any
    other Python construct — expressions containing them are rejected
    with a CalculatorError rather than executed.
    """
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Invalid expression syntax: {exc.msg}")

    return _eval_node(parsed.body)
