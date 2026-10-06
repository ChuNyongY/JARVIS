# File Explorer 폴더 열기 경계

`explorer.folder.open`은 사전에 등록된 `root_id`와 그 루트 아래의 `relative_path`만 받는다.
절대 경로, 드라이브·UNC 경로, `..`, colon이 들어간 구성요소, 존재하지 않는 폴더를 거부한다.
루트부터 대상까지 각 구성요소를 `lstat`으로 확인하며 symbolic link 또는 Windows reparse point가
하나라도 있으면 실행하지 않는다.

대상 identity는 canonical directory path의 SHA-256인
`explorer-folder:<directory_sha256>`이다. 실제 절대 경로는 execution result, verification result,
감사 event에 넣지 않는다. 성공 결과에는 `app_id`, `root_id`, directory digest, PID, HWND,
Windows process creation time만 포함된다.

운영 app registry를 publisher 필수 모드로 먼저 검증한 뒤, 실행 시에는 등록된 `explorer.exe`의
경로와 SHA-256을 executable lease 아래 다시 확인한다. 명령은 shell 없이 고정된 `/n,` 인자와
검증된 폴더 경로만 사용한다. 실행 전 `CabinetWClass` 창 집합을 스냅샷하고, 등록 executable
identity와 일치하는 새 PID/HWND가 정확히 하나일 때만 handoff를 승인한다.

폴더 도달 검사는 별도 `python -I` worker에서 요청당 최대 3초, 최대 3회·0.25초 간격의 bounded
observer로 실행된다. worker는
`Shell.Application.Windows()`에서 요청 HWND를 찾아 `Document.Folder.Self.Path`를 canonicalize한
뒤 예상 폴더와 exact Windows path 비교를 수행한다. 요청에는 내부적으로 예상 경로가 전달되지만
응답에는 boolean과 stable error code만 있으며 경로·창 제목·UI text는 반환하지 않는다.

독립 verifier는 다음을 다시 확인한다.

- action ID와 capability/postcondition 계약
- `root_id`, relative path, target digest와 result binding
- PID의 실행 파일 경로·SHA-256 및 process creation time
- 현재 살아 있는 exact PID/HWND와 `CabinetWClass`
- 새 worker 호출을 통한 exact 폴더 도달

launch 이전 취소와 검증 실패는 입력이 없으므로 retry-safe일 수 있다. Explorer launch 이후의 handoff,
worker, folder mismatch, process identity 실패는 창 생성 여부가 불명확하므로 자동 재시도하지 않는다.
운영 executor는 실패 뒤 임의 창을 닫지 않는다. 실제 smoke 도구만 launch 전 snapshot과 비교해 새로
생긴 신뢰 가능한 후보 또는 성공 결과의 exact HWND에 `WM_CLOSE`를 보내고 최대 3초 안에 실제
소멸했는지 확인하며 기존 Explorer 창은 건드리지 않는다.

현재 Windows 실제 smoke에서는 workspace root 도달, 두 번의 독립 folder check, 경로 비노출,
exact HWND 정리가 모두 PASS했다. 이 증거는 폴더 열기 범위만 입증하며 파일 선택·복사·이동·삭제나
Explorer 내부 임의 UI 조작 권한을 부여하지 않는다.
