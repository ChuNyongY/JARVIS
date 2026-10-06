# 제한된 로컬 기억 검색

`memory.search`는 등록된 로컬 SQLite memory store에 대해 최대 10개의 결과만 검색하는 R0 Native
capability다. action은 `memory-store:<store_id>` target, 256자 이하의 `query`, 선택적 `k=1..10`,
인자 없는 `memory.search.matches` postcondition을 요구한다.

executor 결과는 일치 개수, 결과 전체의 SHA-256 binding, 결과가 하나일 때의 불투명 memory ID만
포함한다. 기억 값, 파일 경로, 검색어와 SQLite 오류 원문은 결과나 감사 로그에 넣지 않는다.
verifier는 같은 bounded query를 독립적으로 다시 실행해 binding 전체가 일치하는지 확인한다.

`MemoryPdfResolver`는 검증된 검색 결과가 정확히 하나일 때만 memory ID를 내부 조회하고, 값이
허용 root 안의 기존 non-symlink PDF인지 확인해 파일 SHA-256에 결합된 `file.open` action을 만든다.
경로와 값은 검색 영수증에 노출하지 않는다. 2026-09-29 actual reversible fixture smoke에서 이
검색→resolver→Edge PDF process/window 경로와 새 창 정리가 모두 통과했다.
