# Hash-pinned PDF open 경계

`file.open`은 허용 root 안의 절대 경로 PDF와 사전 등록된 viewer를 직접 실행한다. Windows file
association이나 shell은 사용하지 않는다. action은 `path`, lowercase SHA-256, `viewer_app_id`를
요구하고 target identity는 `file-sha256:<digest>`, postcondition은 `file.opened`로 고정된다.

PDF는 일반 파일이어야 하며 최종 component symlink, root 밖 경로, `.pdf`가 아닌 확장자, 빈 파일,
기본 512 MiB 초과 파일과 hash 불일치를 거부한다. viewer도 `direct_launch=true`인 등록 앱이어야
한다. Windows에서는 viewer와 PDF 각각의 final path와 hash를 열린 handle에서 확인하고 write/delete
공유를 거부한 상태로 launch까지 유지한다.

launcher는 `shell=False`로 검증된 viewer executable과 검증된 PDF 경로를 전달한다. registry의
`document_launch_mode`가 `browser-new-window`인 viewer만 코드가 고정한 `--new-window`를 추가하며
Planner가 flag를 지정할 수 없다. 성공 결과에는 파일 SHA-256, viewer app ID, PID와 process creation
time, 새 창 검증 시 HWND만 포함하며 PDF 경로는 넣지 않는다. launch 후에는 PID executable/hash와
creation time을 즉시 확인한다. browser mode에서는 launch 전 exact window set을 snapshot하고 새
visible unowned top-level window 하나의 PID/HWND/class/image identity를 안정화 구간 전체에서
확인한다. verifier는 별도 열거로 이를 다시 읽고 PDF hash도 재검증한다.

launch 전 STOP과 identity precheck 실패만 retry-safe다. process 생성 호출이 시작된 뒤 발생한 오류,
잘못된 PID, identity 불명확성과 STOP은 실제 viewer가 열렸을 가능성이 있으므로 자동 재시도를
금지한다.

2026-09-29 실제 S03 fixture smoke는 단일 memory search 영수증에서 hash-pinned PDF action을 만들고,
publisher/hash가 고정된 Edge를 `--new-window`로 실행해 PDF identity, process creation time과 새
window identity를 독립 검증한 뒤 해당 새 창만 닫았다. 첫 actual 시도는 viewer 단계 실패로 집계하지
않았고, cleanup 경계를 보강한 재실행에서 모든 항목이 PASS했다. 제목·URL·화면 내용·memory 값과
경로는 결과에 포함하지 않았다. 이는 fixture PDF 열기 증거이며 사람의 시각적 내용 판독 증거는 아니다.
