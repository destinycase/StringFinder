# StringFinder 버전별 개발 히스토리

8.1.1부터 새로 기록합니다. 7.1.1~7.7.8의 이력은 [v8 이전 개발 히스토리](archive/before-v8/DEVELOPMENT_HISTORY.md), 6.4.20까지는 [v7 이전 개발 히스토리](archive/before-v7/DEVELOPMENT_HISTORY.md)에 있습니다.

## v8.1.2

- 화면 테마를 외부 라이브러리(pyqtdarktheme) 없이 앱 안에 내장했습니다. 다크·라이트 화면은 8.1.1과 픽셀 단위로 같습니다.
- 예전 설정에 남은 "자동" 테마는 Windows의 앱 모드(밝게/어둡게)를 따릅니다(8.1.1과 같은 동작).

## v8.1.1 · 9차 안정 버전

- 7.7.8을 **9차 안정 버전 8.1.1**로 지정했습니다. 코드는 7.7.8과 같고 앱 버전 표기만 바꿨습니다.
- 8 버전부터 검색 엔진의 버전을 앱 버전과 따로 매깁니다(엔진 1.0.0). 창 제목에 `StringFinder v8.1.1 (Engine v1.0.0)`처럼 둘 다 보입니다.
- 7.7.1 이후 바뀐 기능은 [v8.1.1 고정 문서](master-baseline-v8.1.1.md)에 있습니다.
- 8.1.1 선언 뒤 배포 방식을 바꿨습니다. 실행 파일은 GitHub [Releases](https://github.com/destinycase/StringFinder/releases)에서 받습니다(이전 안정 버전 6.4.1~7.7.1 포함). 저장소의 `dist/StringFinder.exe`는 없앴습니다.
