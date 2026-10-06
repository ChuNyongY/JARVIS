# 브라우저 fixture 읽기 경계

`browser.fixture.read`는 네트워크에 접속하지 않고, 생성 시 등록된 로컬 fixture ID와 파일 집합만
읽는 R0 Native capability다. 허용 확장자는 HTML, Markdown, text이며 파일별 1 MiB, 집합 전체
4 MiB, 최대 32개로 제한한다. 절대 경로·상위 경로 이동·symlink·허용 root 밖 파일은 거부한다.

실행 결과에는 원문과 파일 경로를 넣지 않는다. 대신 fixture ID, source count, total bytes와
상대 경로/파일 SHA-256 목록을 다시 해시한 source-set digest만 반환한다. verifier는 파일 집합을
다시 읽어 이 영수증을 독립적으로 확인한다.

이는 S05와 S14의 신뢰되지 않은 고정 자료 입력 경계다. 실제 웹 탐색을 허용하거나 웹 콘텐츠를
명령으로 취급하지 않으며, 문서 작성과 prompt-injection gate의 통합 결과는 별도 검증 대상이다.
