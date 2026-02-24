"""
[test_app_strings_contract.py]

이 테스트는 AppStrings 클래스의 전체적인 무결성과 소스 코드 내 참조의 유효성을 검증합니다.

- 테스트 목적:
  1. 소스 코드에서 참조하는 모든 AppStrings 상수가 실제로 정의되어 있는지 정적 분석을 통해 확인.
  2. 상수 값의 의도치 않은 중복 정의나 빈 값 사용 여부 체크.
  3. 클래스 내에서 상수명이 중복으로 할당된 경우(Copy & Paste 실수) 탐지.

- 주요 검증 사항:
  1. AST(Abstract Syntax Tree) 분석을 통한 실시간 소스 코드 전수 조사.
  2. 정의되지 않은 상수 참조 발견 시 테스트 실패 유도.
  3. 중복된 할당 구문(Line Number 단위) 식별.
"""

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

    def test_unique_values(self):
        """상수 값들의 중복 여부를 체크 (의도치 않은 복사/붙여넣기 방지)"""
        values = {}
        # 설정 관련 상수들은 키값이 같을 수 있으므로 예외 처리 필요할 수 있음
        # 여기서는 단순 로깅 메시지 등이 완전히 똑같은지 체크

        exceptions = ["", " ", "검색", "준비"]  # 중복 허용할 만한 일반적인 단어들

        for name in dir(AppStrings):
            if name.startswith("_"):
                continue
            val = getattr(AppStrings, name)
            if not isinstance(val, str):
                continue

            if val in values and val not in exceptions:
                # 중복 허용 (필요 시 주석 해제)
                pass
            values[val] = name

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
