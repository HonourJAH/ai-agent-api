import pytest

from app.services.tools.calculator import calculate, CalculatorError


class TestCalculatorCorrectness:
    @pytest.mark.parametrize(
        "expression,expected",
        [
            ("47*89", 4183),
            ("(2+3)*4", 20),
            ("2**10", 1024),
            ("-5 + 3", -2),
            ("10 // 3", 3),
            ("10 % 3", 1),
            ("2 + 3 * 4", 14),
        ],
    )
    def test_correct_results(self, expression, expected):
        assert calculate(expression) == expected

    def test_division(self):
        assert calculate("10/4") == 2.5

    def test_division_by_zero_raises(self):
        with pytest.raises(CalculatorError, match="Division by zero"):
            calculate("1/0")


class TestCalculatorSafety:
    @pytest.mark.parametrize(
        "malicious_expression",
        [
            "__import__('os').system('echo pwned')",
            "open('/etc/passwd').read()",
            "(lambda: 1)()",
            "[].__class__.__base__.__subclasses__()",
            "os.system('echo pwned')",
        ],
    )
    def test_rejects_code_injection_attempts(self, malicious_expression):
        with pytest.raises(CalculatorError):
            calculate(malicious_expression)

    def test_rejects_invalid_syntax(self):
        with pytest.raises(CalculatorError, match="Invalid expression syntax"):
            calculate("1; import os")

    def test_rejects_oversized_exponent(self):
        with pytest.raises(CalculatorError, match="too large"):
            calculate("9**9**9**9")

    def test_rejects_string_constants(self):
        with pytest.raises(CalculatorError):
            calculate("'hello'")
