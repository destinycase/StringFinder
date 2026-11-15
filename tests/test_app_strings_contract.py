"""코드에서 쓰는 화면 문구 상수가 모두 문구 표에 있고 이름이 겹치지 않는지 확인합니다."""

import ast
import collections
import os

import pytest

from sf_utils.app_strings import AppStrings


class TestAppStringsContract:
    """TestAppStringsContract 클래스."""

    def test_all_app_strings_references_are_valid(self):
        source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        undefined_refs = []

        defined_constants = set(dir(AppStrings))

        for root, _, files in os.walk(source_root):
            for file in files:
                if not file.endswith(".py"):
                    continue

                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read())

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Attribute):
                            if isinstance(node.value, ast.Name) and node.value.id == "AppStrings":
                                const_name = node.attr
                                if const_name not in defined_constants:
                                    undefined_refs.append(f"{file}:{node.lineno} -> AppStrings.{const_name}")
                except Exception as e:
                    print(f"Skipping file {file}: {e}")

        if undefined_refs:
            pytest.fail("Undefined AppStrings constants found:\n" + "\n".join(undefined_refs))

    def test_no_duplicate_constant_names(self):
        """AppStrings 클래스 내 상수명 중복 정의를 방지한다."""
        app_strings_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "src", "sf_utils", "app_strings.py")
        )
        with open(app_strings_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())

        assign_lines = collections.defaultdict(list)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "AppStrings":
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Name):
                                assign_lines[target.id].append(stmt.lineno)

        duplicates = {name: lines for name, lines in assign_lines.items() if len(lines) > 1}
        if duplicates:
            msgs = [f"{name}: {lines}" for name, lines in sorted(duplicates.items())]
            pytest.fail("Duplicate AppStrings constants found:\n" + "\n".join(msgs))
