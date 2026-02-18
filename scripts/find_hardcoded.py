import os
import ast
import sys


def contains_korean(text):
    for char in text:
        if "\uac00" <= char <= "\ud7a3":
            return True
    return False


def is_suspicious_string(text):
    if not text:
        return False
    # Korean is always suspicious
    if contains_korean(text):
        return True

    # English sentences (with spaces) are suspicious, excluding likely keys or paths
    if " " in text and len(text) > 10:
        # Exclude common false positives
        if text.startswith("SELECT ") or text.startswith("INSERT ") or text.startswith("UPDATE "):
            return False  # SQL
        if text.startswith("{") and text.endswith("}"):
            return False  # f-string format
        if "%s" in text or "%d" in text:
            return False  # legacy format string
        if text.startswith("Q") and any(x in text for x in [" {", ":", ";"]):
            return False  # Qt Stylesheets
        return True

    return False


def check_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception:
        # Retry with cp949 just in case
        try:
            with open(filepath, "r", encoding="cp949") as f:
                tree = ast.parse(f.read(), filename=filepath)
        except Exception as e:
            print(f"Error parsing {filepath}: {e}")
            return

    for node in ast.walk(tree):
        # Handle regular string constants
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            # This is likely a docstring or a standalone string expression
            continue

        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.ClassDef) or isinstance(node, ast.Module):
            # Docstrings are technically Expr(Constant(str)) as first statement
            # But ast.walk doesn't give us list context easily.
            pass

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if is_suspicious_string(node.value):
                # Rough check to see if it's a docstring:
                # If the parent is an Expr which is in a body of ClassDef/FunctionDef/Module at index 0.
                # Since we don't have parent links, we'll just check if it's an Expr.
                # standalone strings in body are often docstrings.
                print(f"{filepath}:{node.lineno}: {repr(node.value)}")

        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if is_suspicious_string(value.value):
                        print(f"{filepath}:{node.lineno}: f-string: {repr(value.value)}")


if __name__ == "__main__":
    src_dir = "src"
    # Setting output to UTF-8
    if sys.platform == "win32":
        import io

        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    excludes = ["sf_utils/app_strings.py", "sf_utils/constants.py", "tests", "logs"]

    for root, dirs, files in os.walk(src_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file).replace("\\", "/")
                rel_path = os.path.relpath(path, src_dir).replace("\\", "/")
                if any(rel_path.startswith(ex) for ex in excludes):
                    continue

                check_file(path)
