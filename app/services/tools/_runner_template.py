import builtins as _builtins
import sys
import traceback

try:
    import resource

    with open("_timeout.txt") as f:
        _timeout_seconds = int(f.read().strip())

    _cpu_limit = min(_timeout_seconds, 30)
    resource.setrlimit(resource.RLIMIT_CPU, (_cpu_limit, _cpu_limit))

    _memory_limit_bytes = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (_memory_limit_bytes, _memory_limit_bytes))
except Exception:
    pass

_SAFE_BUILTIN_NAMES = [
    "abs",
    "all",
    "any",
    "bin",
    "bool",
    "chr",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "map",
    "max",
    "min",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    "True",
    "False",
    "None",
    "Exception",
    "ValueError",
    "TypeError",
    "IndexError",
    "KeyError",
    "ZeroDivisionError",
    "StopIteration",
    "ArithmeticError",
    "OverflowError",
    "RuntimeError",
    "NameError",
    "AttributeError",
]
_safe_builtins = {
    name: getattr(_builtins, name)
    for name in _SAFE_BUILTIN_NAMES
    if hasattr(_builtins, name)
}

_ALLOWED_IMPORTS = {
    "math",
    "json",
    "random",
    "re",
    "datetime",
    "time",
    "statistics",
    "itertools",
    "collections",
    "string",
    "decimal",
    "fractions",
}


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    top_level = name.split(".")[0]
    if top_level not in _ALLOWED_IMPORTS:
        raise ImportError(
            f"Import of '{name}' is not allowed in the code executor sandbox. "
            f"Allowed modules: {', '.join(sorted(_ALLOWED_IMPORTS))}"
        )
    return _builtins.__import__(name, globals, locals, fromlist, level)


_safe_builtins["__import__"] = _restricted_import

with open("user_code.py") as f:
    _source = f.read()

try:
    _code_obj = compile(_source, "user_code.py", "exec")
    exec(_code_obj, {"__builtins__": _safe_builtins})
except Exception:
    traceback.print_exc()
    sys.exit(1)
