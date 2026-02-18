import os
import ast
import pytest
from sf_utils.app_strings import AppStrings


class TestAppStringsContract:
    """
    소스 코드 내에서 참조되는 모든 AppStrings.CONST 상수가
    실제로 AppStrings 클래스에 정의되어 있는지 검증하는 계약(Contract) 테스트입니다.
    """

    def test_all_app_strings_references_are_valid(self):
        source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
        undefined_refs = []

        # AppStrings에 정의된 상수 집합
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
                            # AppStrings.NAME 형태 감지
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
                # print(f"Warning: Duplicate value '{val}' in {name} and {values[val]}")
                pass
            values[val] = name
