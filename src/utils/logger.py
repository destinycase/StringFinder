import logging
import sys



def setup_logger():
    """애플리케이션 전역 로깅 설정"""
    logger = logging.getLogger("StringFinder")
    logger.setLevel(logging.DEBUG)

    # 포맷 설정
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 콘솔 핸들러 (표준 출력)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 파일 핸들러 (UTF-8 인코딩)
    log_file = "string_finder.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# 싱글톤 로거 인스턴스 생성
logger = setup_logger()
