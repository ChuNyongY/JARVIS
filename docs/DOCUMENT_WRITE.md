# 검증된 DOCX fixture 작성

`document.write`는 허용 root 아래에 존재하지 않는 `.docx` 파일 하나를 새로 만드는 R2 Native
capability다. 대상은 정규화된 절대 경로의 SHA-256 identity로 결합하며 title, body와 S05 입력의
source-set digest 전체를 `document.content.sha256` postcondition에 묶는다.

문서는 최소 Open Packaging Convention 구조로 생성한다. 임시 파일을 완전히 기록·flush한 뒤
hard-link 기반 exclusive create를 사용하므로 기존 파일을 덮어쓰지 않는다. 절대 경로가 아니거나
허용 root 밖인 경로, symlink 경로, `.docx`가 아닌 대상, 200자를 넘는 제목과 20,000자를 넘는 본문은
거부한다.

verifier는 생성 파일을 다시 열어 ZIP entry 집합, 중복 entry, XML 크기와 실제 문단 전체를 확인한
뒤 파일 SHA-256 영수증도 다시 계산한다. 실행 결과에는 본문이나 로컬 경로를 포함하지 않는다.
이 구현은 fixture 문서 작성 경계이며 실제 Microsoft Word UI 자동화나 기존 문서 편집을 의미하지
않는다.
