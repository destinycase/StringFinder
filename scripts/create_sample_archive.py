import json
import os


def generate_sample_archive(output_path="sample_data.archive"):
    """
    Archive 특수 검색 테스트를 위한 샘플 .archive (JSON) 파일을 생성합니다.
    """
    data = {
        "Subnamespaces": [
            {
                "Namespace": "System_UI",
                "Children": [
                    {
                        "Key": "ID_BTN_CONFIRM",
                        "Source": {"Text": "Confirm and Proceed"},
                        "Translation": {"Text": "확인 후 진행"},
                    },
                    {
                        "Key": "ID_LBL_WELCOME",
                        "Source": {"Text": "Welcome to StringFinder"},
                        "Translation": {"Text": "StringFinder에 오신 것을 환영합니다"},
                    },
                ],
            },
            {
                "Namespace": "Game_Dialog",
                "Children": [
                    {
                        "Key": "NPC_GREET_01",
                        "Source": {"Text": "Hello, brave traveler!"},
                        "Translation": {"Text": "안녕, 용감한 여행자여!"},
                    },
                    {
                        "Key": "QUEST_DESC_01",
                        "Source": {"Text": "Locate the hidden Abyss Point in the deep cave."},
                        "Translation": {"Text": "깊은 동굴 속 숨겨진 어비스 포인트를 찾으세요."},
                    },
                ],
            },
        ]
    }

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        print(f"성공: 테스트용 파일이 생성되었습니다 -> {os.path.abspath(output_path)}")
    except Exception as e:
        print(f"오류: 파일 생성 중 문제가 발생했습니다: {e}")


if __name__ == "__main__":
    generate_sample_archive()
