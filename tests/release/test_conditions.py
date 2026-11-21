# relationships: validates: release-process
import unittest

from harness import check_condition


class PartConditions(unittest.TestCase):
    def test_early_close_cannot_escape_parent_group(self):
        for expression in ('false) || (true', ')(', 'true)'):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ValueError, 'Unbalanced part condition parentheses'):
                    check_condition(expression)

    def test_missing_close_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unbalanced part condition parentheses'):
            check_condition('(true')

    def test_unterminated_string_is_rejected(self):
        for expression in ("'abc", "'it''s", "'a\\'b'"):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ValueError, 'Unterminated part condition string'):
                    check_condition(expression)

    def test_nested_and_github_quoted_parentheses_are_literal(self):
        for expression in ("((true) || false)", "'(' == '('", "')(' == ')('",
                           "contains('it''s (ready)', '(')", "'''' == ''", r"'a\)' == 'a\)'", 'true'):
            with self.subTest(expression=expression):
                try:
                    check_condition(expression)
                except ValueError as error:
                    self.fail(str(error))
