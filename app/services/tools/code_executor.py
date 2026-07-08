import asyncio
import ast
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 10

# avoid flooding the model's context with runaway print() output
MAX_OUTPUT_CHARS = 4000

_RUNNER_TEMPLATE_PATH = Path(__file__).parent / "_runner_template.py"


class CodeExecutionError(ValueError):
    """Raised when code is rejected before it ever reaches the subprocess —
    currently just dunder attribute access (e.g. ().__class__.__subclasses__()),
    the one gap the restricted-builtins runner alone can't close, since
    attribute access isn't gated by __builtins__ in Python.
    """


class _DunderAttributeVisitor(ast.NodeVisitor):
    def visit_Attribute(self, node: ast.Attribute):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            raise CodeExecutionError(
                f"Access to dunder attribute '{node.attr}' is not allowed"
            )
        self.generic_visit(node)


def _validate_code(code: str) -> None:
    """Static pre-check, layered in front of the runtime restricted-builtins
    sandbox in _runner_template.py. That layer already blocks import,
    open, eval, exec, etc. by omitting them from the exec'd __builtins__ —
    but attribute access like ().__class__ is a language-level feature,
    not a builtin, so no amount of restricting __builtins__ stops it.
    This catches that specific class of escape before the code ever runs.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeExecutionError(f"Invalid Python syntax: {exc.msg}")

    _DunderAttributeVisitor().visit(tree)


async def execute_code(code: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Execute Python code in a sandboxed subprocess and return its output.

    Layered defenses, none of them alone sufficient:
    - Static AST pre-scan (_validate_code) rejects dunder attribute access
      before the code ever runs — closes the one gap the layer below
      can't, since attribute access isn't controlled by __builtins__.
    - Separate OS process (subprocess), not exec() in our own process —
      a crash or hang can't touch the API itself.
    - Restricted builtins inside the executed code (no import, open, exec,
      eval, compile, input, getattr/setattr, dir, etc.) — see
      _runner_template.py for the exact allowed list.
    - OS resource limits (CPU time, memory) as a secondary backstop.
    - `-I -S` isolated Python mode — ignores env vars/user site-packages,
      skips the `site` module, minimizing what's loaded before user code
      even runs.
    - A wall-clock timeout as the final, always-enforced backstop.

    This is still NOT a fully hardened sandbox — no container or OS-level
    process isolation beyond what a plain subprocess + rlimits provide.
    Reasonable for demonstrating a tool-use pattern safely in a portfolio
    project; a real production system running untrusted code would want
    actual container/VM-level isolation (gVisor, Firecracker, or similar).

    Raises CodeExecutionError if the code fails static validation — in
    that case, the subprocess is never started at all.
    """
    _validate_code(code)

    runner_source = _RUNNER_TEMPLATE_PATH.read_text()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "user_code.py").write_text(code)
        (tmp_path / "runner.py").write_text(runner_source)
        (tmp_path / "_timeout.txt").write_text(str(timeout))

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-I", "-S", "runner.py"],
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Execution timed out after {timeout} seconds",
                "exit_code": None,
                "timed_out": True,
            }

        return {
            "stdout": result.stdout[:MAX_OUTPUT_CHARS],
            "stderr": result.stderr[:MAX_OUTPUT_CHARS],
            "exit_code": result.returncode,
            "timed_out": False,
        }
