import pytest

from app.services.tools.code_executor import execute_code, CodeExecutionError


class TestCodeExecutorCorrectness:
    async def test_simple_print(self):
        result = await execute_code("print(sum(range(101)))")
        assert result["stdout"].strip() == "5050"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    async def test_allowed_imports_work(self):
        code = (
            "import math, json, statistics\n"
            "data = {'pi': round(math.pi, 2), 'median': statistics.median([3,1,2])}\n"
            "print(json.dumps(data))"
        )
        result = await execute_code(code)
        assert result["exit_code"] == 0
        assert '"pi": 3.14' in result["stdout"]
        assert '"median": 2' in result["stdout"]

    async def test_syntax_error_rejected_before_execution(self):
        with pytest.raises(CodeExecutionError, match="Invalid Python syntax"):
            await execute_code("this is not : valid python")


class TestCodeExecutorSafety:
    async def test_dunder_attribute_escape_rejected_before_running(self):
        with pytest.raises(CodeExecutionError, match="dunder attribute"):
            await execute_code("().__class__.__base__.__subclasses__()")

    async def test_invalid_syntax_rejected_before_running(self):
        with pytest.raises(CodeExecutionError, match="Invalid Python syntax"):
            await execute_code("def broken(:\n    pass")

    @pytest.mark.parametrize(
        "dangerous_module", ["os", "subprocess", "socket", "sys", "shutil"]
    )
    async def test_disallowed_imports_blocked_at_runtime(self, dangerous_module):
        result = await execute_code(f"import {dangerous_module}")
        assert result["exit_code"] != 0
        assert "not allowed" in result["stderr"]

    @pytest.mark.parametrize(
        "dangerous_code",
        [
            "open('/etc/passwd').read()",
            "eval('1+1')",
            "exec('print(1)')",
            "__import__('os')",
        ],
    )
    async def test_blocked_builtins_fail_at_runtime(self, dangerous_code):
        result = await execute_code(dangerous_code)
        assert result["exit_code"] != 0

    async def test_infinite_loop_is_contained(self):
        """Two independent safety layers can stop a runaway loop: the
        wall-clock timeout (subprocess.run's own timeout=), or the CPU
        rlimit inside the subprocess. Which one fires first is a race
        depending on process/scheduling overhead — not worth pinning
        down. What matters is containment happens one way or the other,
        since a genuine infinite loop can never exit with code 0.
        """
        result = await execute_code("while True: pass", timeout=3)
        assert result["timed_out"] is True or result["exit_code"] != 0

    async def test_output_is_truncated(self):
        from app.services.tools.code_executor import MAX_OUTPUT_CHARS

        result = await execute_code("print('x' * 10000)")
        assert len(result["stdout"]) <= MAX_OUTPUT_CHARS
