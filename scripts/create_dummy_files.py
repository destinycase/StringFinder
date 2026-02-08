import os
import random
import string


def create_dummy_files(target_dir, count=1000):
    """
    테스트를 위한 더미 파일 1000개 생성 (확장자별 형식 준수)
    """
    extensions = ["txt", "log", "xml", "json", "xlsx", "xlsm"]
    search_keyword = "pro"

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"Created directory: {target_dir}")

    print(f"Generating {count} files in {target_dir}...")

    for i in range(count):
        ext = random.choice(extensions)
        filename = f"test_file_{i:04d}.{ext}"
        filepath = os.path.join(target_dir, filename)

        content = ""
        has_keyword = random.random() < 0.1
        keyword_injection = f" [FOUND: {search_keyword}] " if has_keyword else ""

        if ext == "json":
            data = {
                "id": i,
                "name": "".join(random.choices(string.ascii_letters, k=8)),
                "description": f"Random dummy data{keyword_injection}",
                "values": random.sample(range(100), 5),
            }
            import json

            content = json.dumps(data, indent=4)
        elif ext == "xml":
            content = f'<?xml version="1.0" encoding="UTF-8"?>\n<note>\n  <id>{i}</id>\n  <msg>Dummy XML message{keyword_injection}</msg>\n</note>'
        elif ext == "log":
            levels = ["INFO", "DEBUG", "ERROR", "WARNING"]
            lines = []
            for _ in range(20):
                lvl = random.choice(levels)
                msg = "".join(random.choices(string.ascii_letters + " ", k=30))
                if has_keyword and len(lines) == 10:
                    msg += keyword_injection
                lines.append(f"2026-02-08 12:00:00 [{lvl}] {msg}")
            content = "\n".join(lines)
        elif ext in ["xlsx", "xlsm"]:
            try:
                from openpyxl import Workbook

                wb = Workbook()
                ws = wb.active
                ws.title = "DummyData"
                for r in range(1, 21):
                    val = "".join(random.choices(string.ascii_letters + " ", k=30))
                    if has_keyword and r == 10:
                        val += keyword_injection
                    ws.cell(row=r, column=1, value=val)
                wb.save(filepath)
            except ImportError:
                # openpyxl이 없는 경우 fallback (텍스트 저장)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(f"Dummy Excel Content{keyword_injection}")
        else:  # txt
            lines = []
            for _ in range(20):
                line = "".join(random.choices(string.ascii_letters + " ", k=50))
                if has_keyword and len(lines) == 5:
                    line += keyword_injection
                lines.append(line)
            content = "\n".join(lines)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

    print(f"Successfully created {count} formatted dummy files.")


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(current_dir)
    dummy_data_dir = os.path.join(project_root, "tests", "dummy_data")

    create_dummy_files(dummy_data_dir)
