import ast
import unittest


class TestPythonSyntaxExamples(unittest.TestCase):

    def test_valid_python(self):
        code = "def add(a, b):\n    return a + b\n"
        ast.parse(code)

    def test_invalid_python(self):
        code = "def add(a, b)\n    return a + b\n"
        with self.assertRaises(SyntaxError):
            ast.parse(code)

    def test_bare_except_detection(self):
        code = "try:\n    pass\nexcept:\n    pass\n"
        tree = ast.parse(code)
        handlers = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and node.type is None
        ]
        self.assertEqual(len(handlers), 1)


if __name__ == "__main__":
    unittest.main()