# 로컬 메모리 계약

`SqliteMemoryRepository`는 기본 semantic/episodic 기억을 stdlib SQLite로 저장한다. semantic
값은 JSON scalar 한 개만 허용하고 key, source, confidence, LOW/MEDIUM sensitivity, UTC 생성·사용·
만료 시각을 함께 보관한다. 같은 key를 다시 저장하면 ID를 유지한 채 갱신한다. 사용자는 ID로
삭제할 수 있다.

episodic memory는 원문 transcript나 action arguments가 아니라 최대 1,000자의 작업 요약,
task ID, SUCCESS/FAILED/ABORTED, 최대 16개 app ID만 저장하며 기본 30일 뒤 만료된다. 만료된
레코드는 조회에서 제외되고 `purge_expired`로 제거한다.

password/API key/bearer/private-key 형태는 semantic key/value와 episode summary에서 거부한다.
HIGH sensitivity 형식 자체를 제공하지 않는다. 이 검사는 credential manager를 대신하지 않으며
모든 민감정보를 의미적으로 판별한다고 주장하지 않는다. 저장 후보는 명시적 사용자 지시 또는
검증된 task 결과에서만 생성해야 한다.

검색은 raw query를 FTS5에 넘기지 않는다. 영문·숫자·한글 token을 최대 16개 추출해 quoted
literal AND 식을 생성하고 모든 SQL 값은 parameter binding한다. k는 최대 50이다. DB는 absolute
non-symlink path, schema version, 최대 row·file size를 강제한다. WAL을 사용하지 않고 SQLite
`max_page_count`로 쓰기 전 파일 상한을 적용한다. Windows에서는 DB 생성 직후 현재 사용자와
LocalSystem만 허용하는 protected DACL을 적용한다.
