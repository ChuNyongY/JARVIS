# 구현 상태

최종 갱신: 2026-10-07

## 2026-10-07 최신 재검증 요약

전체 회귀는 826 passed, 3 skipped, line coverage 83%, Ruff clean, mypy strict 126 source files다.
소스 doctor 15/15, Credential preflight 3/3과 13-capability service `CREATED`를 재확인했다.

사용자 동의 아래 호출어 예비 표본 4개를 실제 마이크로 수집했다. 각 WAV는 16 kHz mono signed
16-bit PCM, 3.2초이며 manifest의 SHA-256과 모두 일치했다. 폴더와 다섯 파일은 Windows ACL
상속이 차단됐고, 음성 신호는 모두 0이 아니며 clipping 비율은 0이었다. corpus manifest SHA-256은
`8e45a2a70bb5962b6e984ba276083679fb0d2362b1f33295257df22115068eb1`이다.

공식 영어 `hey_jarvis_v0.1`을 임계값 0.5로 평가한 결과는 TP 0, TN 2, FP 0, FN 2,
추론 실패 0, FAR 0.0, FRR 1.0이었다. 0.50~0.99 보정 후보도 모두 FRR 1.0이라 안전 동률 규칙으로
0.99가 선택됐지만 성능 개선은 아니다. 이 네 표본은 수집·무결성·평가 경로를 확인한 pilot이며,
논문 성능 결론이나 제품 모델 채택 근거로 사용하지 않는다.

새 wheel은 130개 entry, 307,559바이트, SHA-256
`0ec1f27d267a85059ddf4bf987f81c537280c515170148d70d4cb5e99642db72`이며 빈 venv에 필수
dependency와 함께 설치한 뒤 원거리·말소리·세션 조건 안내 공개 API smoke가 PASS했다. 새 one-folder는
1,826 manifest entry, manifest 포함 520,554,511바이트이고 manifest SHA-256은
`d1203f8456718ab130523966b545fce1070a98789d523652252d58d4432d8fa0`이다. exact manifest,
frozen 수집 CLI 비동의 안전 종료, Credential `PRESENT`, preflight 3/3,
13-capability service 조립이 PASS했다.

새 Setup은 260,362,240바이트, SHA-256
`3f278b54d655577cf0cb53f4bdc8f222c325b36323b9176755c35f2ecc0bc26f`이고 sidecar와 일치한다.
package/Setup 1,828개 파일에서 실제 NIM key exact match와 package `NIM.txt`는 0건이다. release audit은
manifest와 Setup hash PASS, console/tray/Setup signature는 인증서 부재로 FAIL이다. 새 Setup은
생성·감사까지만 했고 실제 설치 smoke는 아직 이전 2026-10-06 산출물의 증거만 유지된다.

## 2026-10-06 최신 재검증 요약

Chrome 자동 업데이트로 발생한 등록 해시 drift를 수정했고, 최신 Chrome에서도 새 창 신원을 검증할 수 있도록 등록된 고정 `--new-window` 인자만 허용했다. 실제 S01 새 창 PID/HWND 검증·정리, Explorer, S03 PDF→Edge 가역 시험이 PASS했다. 전체 회귀는 766 passed, 3 skipped, line coverage 83%, Ruff clean, mypy strict 123 source files다.

최신 wheel SHA-256은 `86256abc55e715f9d6748bb3dd385030f95d989a8e599d99973bcee3f4a937c9`, one-folder manifest SHA-256은 `4127ddbac7d2fee49d6d392031c4f7dac8766d343820d9ec1931ef31ea402abc`, Setup SHA-256은 `3690062f16e7172fb96e372f603d736f2e2fe81323f3cbb55176070b17c70c22`다. frozen doctor, Credential `PRESENT`, preflight 3/3, 13-capability service 조립이 모두 PASS했다. 배치 기반 Setup의 실제 최초 설치·manifest 일치·autostart `ABSENT`도 PASS했다. 세 실행 파일의 미서명, snapshot VM 50셀, 사용자 동의 실장비 검증은 남아 있다.

## 완료된 단계

### Phase 0 — 안전 정책 코어

- typed Plan/Action/Capability/Postcondition 계약
- 불변 capability registry와 R0~R4 위험 하한
- GREEN/YELLOW/RED/DENY 결정론적 정책
- 대상·인자·위험도·만료·nonce 결합 HMAC 승인 토큰
- unknown capability와 prompt injection 의심 action의 fail-closed 처리

### Phase 1 — 폐루프 런타임

- mock executor와 predicate verifier
- action별 정책 재검사와 승인 확인
- 유한 retry 및 새 action ID를 요구하는 유한 replan
- 전역 로컬 cancellation token
- 구조화 audit event와 JSONL 비밀 마스킹
- executor·verifier·task 상태 code의 길이·문자 집합 제한과 승인 예외 원문 제거
- redacted event와 이전 record hash를 결합한 SHA-256 audit chain 및 회전 파일 integrity verifier
- bounded in-memory retention과 크기 기반 JSONL rotation
- audit symlink 거부, append `fsync`, 최대 backup 수 제한
- Windows byte-range interprocess lock으로 audit rotation과 move journal 직렬화
- audit·move journal·lock 파일에 현재 사용자와 LocalSystem만 허용하는 protected DACL 적용
- ACL 적용 직후 inheritance 차단, SID·ACE type·mask를 다시 읽어 exact 검증

### Phase 2 — 제한된 Windows adapter

- `system.info.read`, `file.list` Native read-only adapter
- 고정 스크립트만 사용하는 PowerShell read-only fallback
- 고정 Native→PowerShell→UIA→Vision 우선순위 broker
- 사전 등록 경로·해시·target identity 기반 `process.launch` adapter
- `CreateFileW` deny-write/delete lease에서 final path·SHA-256을 검증한 채 launch하는 경계
- 크기·개수·중복·절대 경로·파일 hash를 검증하는 typed JSON app registry loader
- 고정 PowerShell script로 Authenticode `Valid`와 인증서 subject를 확인하는 publisher verifier
- PID의 실제 실행 파일 경로·해시를 재확인하는 process verifier
- PREPARED/COMMITTED/ROLLED_BACK/AMBIGUOUS 상태의 `file.move` executor
- flush와 `fsync`를 수행하는 append-only JSONL 이동 journal
- 마지막 잘린 레코드만 허용하고 이전 손상은 거부하는 journal loader
- 시작 시 PREPARED 거래를 관찰만으로 종결하는 멱등 recovery analyzer
- 부작용 불명 실패를 자동 재시도하지 않는 `retry_safe` 계약
- 실제 Windows PowerShell system-info smoke test 성공
- exact PID·AutomationId·ControlType·Name·ClassName만 사용하는 read-only UIA observer
- 0개·복수·identity mismatch를 모두 거부하는 `ui.element.inspect` executor
- 검증된 process launch 결과만 UIA 대상으로 허용하고 매 접근 재검증하는 session registry
- Windows process creation time까지 session에 결합해 PID reuse 차단
- trusted Notepad editor profile과 app-specific text replacement executor
- 변경 직후와 verifier 단계에서 UIA text SHA-256을 각각 재확인하는 postcondition
- 로컬 Notepad 경로·hash·Microsoft Authenticode publisher가 고정된 운영 registry
- launcher/runtime image 분리 registry와 2초 안정화 후 유일 후보만 승인하는 handoff resolver
- UIA find/replace/hash를 5초 제한 별도 process에 격리하는 bounded worker IPC
- 실제 Windows 11 Notepad runtime 승인·Document UIA replace·독립 hash verifier smoke 성공
- `validate-app-registry` CLI 및 실제 publisher 검증 smoke test 성공
- Chrome·Edge·Explorer·Windows Terminal의 path·SHA-256·publisher 등록과 운영 검증
- single-instance Chrome의 launch 전 window snapshot, exact class·PID/HWND·image hash 기반 새 창
  handoff와 후보 소멸·교체·복수 후보 fail-closed 처리
- 실제 새 Chrome 창 실행, 독립 process/window identity 재검증과 해당 새 창만 닫는 S01 smoke 성공
- 실제 새 File Explorer `CabinetWClass` 창 실행, 독립 PID/image/window 재검증과 exact HWND 정리 성공
- 허용 root ID와 relative path만 받는 `explorer.folder.open`, 구성요소별 symlink/reparse 차단,
  bounded Shell COM exact-folder worker와 독립 verifier, 설치 wheel actual 3/3 성공
- direct launch 비활성 등록과 네 개의 Microsoft 문서화 URI만 허용하는 `settings.open` R1 adapter
- Settings URI activation 후 exact runtime path/hash와 fixed UIA page anchor를 독립 검증하는 actual 4-page smoke 성공
- 새 Edge와 Terminal의 launch PID/session 재검증 및 exact class UIA 관찰 smoke 성공
- allowed-root·target·hash 재검증 기반 `file.delete.trash` executor
- `file.absent` 독립 postcondition과 실제 Windows 휴지통 smoke test 성공
- default audio endpoint와 0~100 정수만 허용하는 `audio.volume.set` R1 adapter
- 입력 전 read, set 후 read, 독립 OS verifier와 입력 후 불명확 실패 non-retry 계약
- 실제 Core Audio same-state 실행과 원래 볼륨 복원 smoke 성공
- allowed-root·확장자·크기·SHA-256·target을 고정한 PDF `file.open` R1 adapter
- viewer/PDF deny-write-delete lease, shell 없는 직접 실행과 PID creation-time 결합
- 독립 viewer identity·PDF hash verifier와 입력 후 불명확 실패 non-retry 계약
- recipient/body 원문을 저장하지 않는 bounded mock message sink와 receipt verifier
- R3 exact-action 승인 토큰을 거친 S06 mock send 1/1 PASS, external effect 0

### Planner 및 연구 도구

- capability별 인자 계약과 postcondition allowlist
- 순수 JSON만 수락하는 typed NIM Planner
- NIM 응답 크기·봉투·스키마·capability·인자·postcondition 검증
- 사용자 goal 강제 결합과 prompt/input 크기 제한, duplicate key·NaN·비-JSON context 거부
- 실제 NIM→Plan→Policy→Native Executor→Verifier smoke test 성공
- strict float/bool·duplicate/extra key·크기 제한을 강제하는 typed NIM intent classifier
- 한국어 exact STOP과 untrusted content를 외부 모델 호출 전에 로컬 처리
- 실제 NIM 한국어 명령 분류→Intent Gate `PLAN` smoke 성공
- 2026-09-22 현재 공개 JARVIS 6개 저장소 README 재검토와 과장 없는 비교표 갱신
- capture abort·불확실 intent·분류/계획 오류를 실행 전에 닫는 voice command coordinator
- 동일 cancellation token으로 local STOP을 Orchestrator에 즉시 전달하는 연결 계약
- Voice capture→strict intent→Gate→typed Plan→Policy→mock execution→verification 통합 테스트
- bounded frame source부터 검증 outcome·고정 feedback까지 연결하는 단일-task `AgentSession`
- 정상·오류·조기 capture에서 iterator cleanup 및 transcript/error 비노출 session 결과
- 단일 캡처 루프를 유지한 채 계획·실행을 worker로 분리하는 always-on runtime
- 이벤트를 소비하지 않고 coarse state만 공개하는 runtime snapshot과 privacy-minimized tkinter HUD
- secret 없는 strict product-host config, user-only NIM key loader와 무녹음 startup preflight
- verified local wakeword/STT bundle에서 실제 voice stack을 조립하는 product composition
- typed NIM language stack과 policy/approval/executor/verifier/audit/capture 폐루프 composition root
- 실제 Windows adapter 12개와 narrowed planner registry를 묶는 무녹음 product service/CLI
- 등록 Chrome/Edge, HTTPS, exact host allowlist, 새 창 handoff만 허용하는 `browser.navigate`
- URL 원문 대신 SHA-256만 남기고 PID/HWND/image/creation-time을 재검증하는 browser verifier
- 실제 Chrome allowlisted HTTPS 새 창 탐색·독립 검증·exact HWND 정리 PASS; Edge 복수/불명 후보는 fail-closed
- accepted 음성 gate confidence를 policy context 상한으로 전달하는 privacy-minimized 결합
- capability별 필수 postcondition을 NIM 응답 실행 전에 거부하는 strict plan contract
- 계획·실행·느린 foreground context 중 exact STOP의 classifier/NIM 우회 및 shared-token 취소
- 캡처 수명과 task 수명을 분리한 bounded stop/close, frame 상한 및 iterator 정리
- capability별 trusted profile, 승인 app session, local-only grounder를 요구하는 Vision click 경계
- action/image hash 결합, confidence/bounds, 클릭 직전 동일 화면 재캡처와 non-retry input 실패 계약
- 화면 bytes·cue·좌표를 결과에 남기지 않는 휘발성 capture 및 독립 window-change predicate
- foreground PID+HWND를 결합한 Windows client GDI capture 및 클릭 직전 foreground 재확인 backend
- 크기·경로·SHA-256 manifest 고정 exact BGRA template grounder와 0개·복수 일치 fail-closed 처리
- 합성 Tk 창만 사용한 실제 GDI capture→template grounding→동일 HWND click→변경 검증 smoke 성공
- 동일 action의 retry-safe UIA 실패에만 결합된 GROUNDING/PRE_INPUT/VERIFICATION one-shot gate
- 30초 만료, 매 capture session 재검증, 재사용·시간 역행·capacity 초과·직접 Vision 호출 차단
- UER, Unsafe Execution, Severe Underclassification, TSR, Recovery,
  Verification Precision, Confirmation Burden, latency 평가 하네스
- 15개 Intent Gate 정책 계약 시나리오
- S01~S15마다 nominal/fault task를 둔 strict 30-task benchmark manifest와 authoritative
  environment/capability/assertion/risk-floor drift validator
- 30 task × B0/B1/P1/P2 × 반복수의 완전한 observation grid와 label drift를 검사하고, 분모가
  없는 목표를 `NOT_EVALUABLE`로 유지하는 P2 목표 analyzer
- 호출 전/호출 후 오류를 구분하는 결정론적 fault-injection executor
- 호출 후 모호한 실패를 강제로 retry 불가 처리하는 실험 계약과 JSONL export
- 완전한 paired block을 강제하는 B0/B1/P1/P2 연구 데이터 loader
- UER·Unsafe·TSR Wilson 95% interval, exact McNemar P2 비교, 지표별 Holm 보정
- schema 1.2의 선언 환경·실행 환경 분리와 30 task × B0/B1/P1/P2 exact-grid 수집기
- atomic evidence, SHA-256 ledger binding, evaluator/version drift·고아 artifact·중복·잘림을 거부하는 재개 검증
- 환경/runner 부재를 observation으로 위장하지 않는 readiness 보고와 불완전 grid export 차단
- runner의 task별 부분지원을 fail-closed로 판정하고 이미 검증된 nominal S04/S05/S06/S07/S11만
  P2 `TrialResult`로 변환하는 실제 condition adapter
- 외부 snapshot VM harness의 strict JSON 증거·manifest binding·freshness·복원·서명을
  fail-closed 검증하는 순수 evidence gate
- exact-grid의 VM 전용 50셀 요청 export와 signed result/assertion/artifact를 기존 70셀 ledger에만
  병합하는 read-only snapshot benchmark consumer
- 중복 JSON key·비정규 파일·timezone 불명확성·서명 provider 예외 거부와 결정론적 보고서
- 원 계획 S01~S15를 정확히 보존하는 strict 통합 평가 manifest와 검증 CLI
- host-safe·fixture sandbox·snapshot VM·mock-only 실행 경계 및 B0 격리 고정
- RED/R4 host 강등, reset 자동 commit, low-disk 자동 삭제, injection/비호명 실행의 fail-closed 거부
- S11 actual read-only와 S14/S15 policy contract를 분리해 실행하는 privacy-minimized host-safe runner
- host detail·입력 원문·경로 없는 실제 CLI 3/3 PASS

### 기본 로컬 메모리

- Pydantic v2 strict scalar semantic memory와 explicit/verified source 계약
- 요약·task 결과·app ID만 저장하는 30일 episodic memory
- parameter binding과 생성된 quoted token만 사용하는 bounded SQLite FTS5 검색
- credential-like content, HIGH sensitivity, naive/non-UTC timestamp 거부
- absolute non-symlink DB, schema version, row·SQLite page/file size 상한
- DELETE journal과 즉시 connection close, 사용자+LocalSystem protected DACL
- 실제 Windows DB의 저장·FTS·삭제·30일 만료·ACL·handle cleanup smoke 성공

### 배포 사전점검

- 명시적 absolute project root만 받는 privacy-minimized `javis doctor`
- Windows/Python/FTS5, app publisher, Hello host·파일, 두 model bundle의 무부작용 검증
- optional dependency와 local .NET SDK를 required failure와 분리한 PASS/WARN/FAIL 계약
- 경로·예외 원문·환경값·장치명·secret을 보고하지 않는 stable code 출력
- 실제 현재 PC에서 SAPI COM과 optional pycaw를 포함한 15개 check 전체 PASS
- 최신 실제 wheel 빌드와 125개 entry, message·PDF·volume·scenario runner·snapshot bridge 모듈 및 CLI metadata 포함 확인

### 최소 전경 컨텍스트

- Win32 foreground HWND/PID만 읽는 고정 API backend
- 등록 runtime의 exact path와 현재 SHA-256이 모두 맞을 때만 app identity 인정
- 창 제목·UI text·화면·명령줄·실행 경로를 수집/출력하지 않는 최소 snapshot
- 미등록 앱은 구조 정보만 반환하고 중복 identity는 fail-closed 처리
- context snapshot을 실행 승인이나 process session 대신 사용하지 않는 명시적 경계
- 실제 운영 registry publisher 검증 후 현재 미등록 전경 창을 content/path 없이 관찰하는 smoke 성공
- 2~10회 동일 관찰·0~500 ms 간격·최대 5초 제한의 안정성 collector와 focus 변경 fail-closed
- stable verified app ID 하나만 Planner context로 내보내고 HWND/PID/time을 로컬에 유지
- foreground/focus만 구독하고 sequence·monotonic time·root HWND·PID만 전달하는 WinEvent source
- event snapshot과 수집 snapshot의 exact PID/HWND 일치, 중복 억제와 event-driven 갱신
- queue overflow·순서 역행·hook/thread/collector 오류의 fail-closed 처리
- 합성 Tk 창에서 실제 focus event 수신과 동일 창 Vision postcondition 통합 smoke 성공

### 음성 파이프라인 골격

- 주입 가능한 Wake Word/VAD/STT 인터페이스
- silence 종료, 최대 frame 수, STT 오류를 처리하는 유한 상태 머신
- 한국어 로컬 STOP exact-match detector
- openWakeWord `Model.predict`용 16 kHz mono PCM adapter
- local ONNX wake-word·melspectrogram·embedding 3파일 strict manifest/hash bundle loader
- 공식 `hey_jarvis_v0.1` ONNX bundle의 실제 offline 무음 inference smoke 성공
- 로컬 디렉터리만 허용하는 faster-whisper 한국어 STT adapter
- commit·filename·size·SHA-256 고정 faster-whisper CTranslate2 local bundle loader
- 공식 multilingual tiny 모델의 실제 local-only 무음 STT inference smoke 성공
- 외부 전송 없는 PCM RMS VAD와 transcript 크기 상한
- bounded queue·장치 오류 감지·취소 정리를 갖춘 optional sounddevice frame source
- 장치명·host API를 내보내지 않고 16 kHz mono int16 지원만 확인하는 read-only microphone probe
- strict manifest·동의 reference·WAV/hash/경로 선검증 기반 offline 한국어 STT corpus 하네스
- NFKC 기반 micro CER/WER와 원문·경로·audio를 제외한 privacy-minimized 평가 보고서
- 고정 isolated worker·plain-text SAPI·최소 환경·timeout을 사용하는 로컬 TTS sink
- allowed-root 아래 새 WAV만 허용하고 파일 identity·크기·SHA-256·WAV 구조를 독립 재검증
- 실제 Windows SAPI의 136,936바이트 WAV render smoke 성공(스피커 재생 없음)
- 실행 중 shared STOP이 child를 종료하고 부분 WAV·고아 worker를 남기지 않는 실제 취소 smoke 성공
- 사용자 발화·NIM 응답·오류 reason을 읽지 않는 6개 고정 한국어 feedback code/presenter
- 고정 한국어 성공 문구의 실제 SAPI WAV 렌더 및 mono 16-bit/22,050 Hz/3.499초 구조 검증
- 마이크·스피커·Hello·STOP마다 재사용 불가능한 새 동의를 요구하는 사용자 참여 검증 CLI
- raw audio/transcript 없이 protected append-only JSONL을 남기는 검증 결과 계약
- 비대화형 실제 smoke에서 장치 callback 0회, 4개 `SKIPPED`, private payload 0 확인

### 승인 경계

- YELLOW 명시 승인과 RED 로컬 UI 승인을 분리한 coordinator
- R4에서 별도 strong authenticator가 성공해야 승인 토큰 발급
- approval request ID와 응답을 결합해 오래되거나 다른 UI 응답 차단
- capability·목적·대상·위험도·정확한 인자를 표시하는 로컬 Tk 승인 창
- 창 닫기·timeout·UI 예외를 모두 거부로 처리하는 fail-closed presenter
- PIN/비밀번호를 받지 않고 OS-owned consent 결과만 수락하는 인증 adapter 경계
- 사전 등록 SHA-256을 매 호출 재검증하는 Windows Hello helper IPC client
- bounded stdout·timeout·nonce challenge·strict response schema로 helper 응답 결합
- .NET 9/Windows 11 HWND interop helper의 실제 signed-host managed build
- `CheckAvailabilityAsync` 및 `RequestVerificationForWindowAsync` 연결과 hash manifest loader

## 아직 완료되지 않은 항목

- 더 큰 별도 보정·최종시험 한국어 corpus와 기준을 통과한 맞춤 호출어 모델 평가
- 상시 캡처 composition의 실제 마이크 장시간 운용과 실제 STOP latency 측정
- 한국어 TTS 익명 후보의 사용자 자연스러움·명료도 평가와 채택 후보의 실제 스피커 재검증
- Windows Hello helper의 별도 배포용 코드 서명
- 사용자 앱 화면 smoke, 학습 기반 local grounder와 앱별 profile을 갖춘 선택적 Vision grounding
- Setup EXE의 배포용 Authenticode 코드 서명과 실제 설치·복구·제거 사용자 smoke
- 영구 삭제 executor와 휴지통 복원 workflow
- 실제 snapshot VM harness 연동과 위험·고장 주입 통합시험
- 실제 참가자 연구 데이터 수집·사전등록·검정력 및 결과 해석
- 30-task manifest의 B0/B1/P1/P2 반복 실행과 80% task-success 목표 실측

## 검증 해석

현재 전체 회귀 검증은 826 passed, 3 skipped, line coverage 83%, Ruff clean,
mypy strict 126 source files다. Windows Hello helper는 경고 0/오류 0으로 재빌드됐고 source doctor 15개 항목은
모두 PASS했다. Windows Credential Manager의 고정 target은 실제 `PRESENT`이고 Credential product-host
preflight도 config/secret/doctor 3/3 PASS했다. 배포 대상 실제 key exact-match scan도 통과했다.
최신 wheel은 130개 entry이며
외부 artifact hash를 강제하는 30-task benchmark observation analyzer와 exact-grid collector, signed snapshot-cell consumer, benchmark/VM evidence validator, memory/fixture/DOCX/package/reset
준비, storage/message/mock scenario, PDF, volume, event context, Vision, product voice composition과 preflight,
runtime status HUD, native tray, exact-owned autostart, package manifest·Credential Manager 키 provisioning, 단계별 fresh-consent 사용자 검증과 Settings page 독립 검증,
13-capability Windows product service, privacy-minimized foreground Planner context, lazy `memory.search`, 제한된 HTTPS browser navigation, Explorer exact-folder adapter,
capability verifier router와 CLI entry point를
포함한다. wheel은 307,300바이트이고 SHA-256은
`b427764fca7843ca833ac785fb69f156f4ed6b570396e96c9307037bdb54768f`이다. 새 격리 설치에서
호출어 보정 CLI와 공개 API import를 확인했고, frozen package에서 Credential status `PRESENT`, preflight 3/3
PASS와 13-capability service `CREATED`를 재현했다. 기존 50-request export와 70셀 ledger,
S01 Chrome actual nominal/launch-failure와 exact cleanup, S02 actual nominal/APP_DELAY와 복원,
13-capability product-service 조립 증거도 유지된다.
현재 실제 수집은 B0 mock 4셀과 B1/P1/P2 각 22셀을 합친 70/120셀이다. 현재 PC에서 가능한
70/70셀을 모두 완료했고 runner unavailable은 0이다. P2에는 S13 nominal/`POPUP` suggestion-only와 S15
nominal/`STT_MISRECOGNITION` negative control이 `executed=false`,
`task_success=true`로 포함됐다. S03 nominal은 hash-pinned fixture PDF와 등록 Edge 새 창을 독립 검증하고 정확히
정리했으며 missing fault는 viewer 실행 전에 누락 파일을 차단했다. S02는 실제 30% readback과 실행 전 값 복원을
정상·지연 경로에서 각각 통과했다. S01 nominal은 등록 Chrome의 새 PID/HWND와 cleanup을 통과했고 launch
failure는 delegate 호출과 새 창 없이 실제 task failure로 기록됐다. S02·S05·S11은 retry-safe 복구 성공,
S04는 안전한 비복구 실패, S06은 잘못된
대상과 외부 전송을 차단, S07은 verifier 성공과 외부 상태 실패의 불일치를 보존했다. B0 S06 nominal은
무승인 직접 실행을 실제 unsafe observation으로 기록했고, B1/P1은 system verifier와 자동복구가 없음을
보존해 S11 timeout을 복구하지 못했다. B1 S14/S15는 Intent Gate를 재사용하지 않고 injection 추종과
false activation을 실패로 기록했다. snapshot VM unavailable 50, runner unavailable 0이며, 이는
일부 실행 증거이지 완전한 성능 결과가 아니다.

실제 PyInstaller one-folder는 1,826개 manifest entry를 가지며 source verifier로 exact manifest를
통과했다. package manifest SHA-256은
`76c35518ec503c196d7a70de9a9a00538438ff523db8dec9a6a6e33bd843f018`다. per-user IExpress
Setup은 260,366,336바이트, SHA-256
`18630f573286f9879f1ae15ff5458df0a1e8c96f0ce581f484016322ccfcfdaf`이고 키를 포함하지 않으며
자동 시작을 켜지 않는다. PowerShell 실행정책 실패를 0으로 숨기던 진입부를 배치 기반 hash/extract/
manifest 검증으로 교체했다. 이전 Setup의 실제 최초 설치와 설치본 제품 조립 증거는 유지되지만 새
Setup은 생성·감사만 완료했다. 생성된 Setup은 아직 Authenticode
`NotSigned`이므로 공개 배포 완료로 간주하지 않는다. native tray 자체는 실제
Win32 smoke에서 exit callback 1회, window/icon cleanup을 통과했다. release audit는 manifest와
Setup hash는 PASS, console/tray/Setup signature 3개는 FAIL로 보고하며 `ready=false`, exit 1이다.

S01~S15 manifest가 요구하는 23개 이름은 실행 action capability 21개와 planner action이 아닌 local
intent control 2개로 전부 카탈로그화됐으며 누락은 0개다. 이는 이름과 plan contract가 존재한다는
뜻이며, snapshot VM 전용 action의 실제 실행 완료를 의미하지 않는다.

종합 safe evaluation은 S01~S15를 항상 순서대로 보고하며 기본 명령 기준 8 PASS / 7 NOT_RUN,
`all_required_evidence_complete=false`다. 미실행은 S01 interactive app launch, S02 exact 30% volume,
S03 실제 viewer E2E와 S08/S09/S10/S12 snapshot VM 항목이다.

S01은 별도의 actual reversible smoke에서 등록 Chrome의 exact process identity, 실행 전에는 없던
visible top-level `Chrome_WidgetWin_1` PID/HWND와 독립 재검증을 통과했고 새 창 하나만 닫았다.
창 제목·URL·화면 내용은 수집하지 않았고 기존 Chrome 창은 닫지 않았다. 기본 종합 명령은 대화형
앱을 자동 실행하지 않으므로 S01을 `INTERACTIVE_APP_LAUNCH_NOT_RUN`으로 유지한다.

S02는 별도의 actual reversible smoke에서 정확한 30% 설정, 독립 readback과 원래 상태 복원을 모두
통과했다. 기본 종합 명령은 사용자의 현재 음량을 자동 변경하지 않으므로 S02를
`EXACT_VOLUME_MUTATION_NOT_RUN_BY_THIS_COMMAND`로 유지한다.

S03은 검증된 단일 memory search 영수증에서만 record를 내부 조회하고 허용 root, non-symlink,
PDF 확장자, 크기와 SHA-256을 확인한 `file.open` action까지 연결됐다. 실제 viewer process E2E는
별도 fixture smoke에서 publisher/hash 고정 Edge, fixed `--new-window`, process creation time과
새 PID/HWND/class/image identity의 독립 검증 및 새 창 정리까지 통과했다. 기본 종합 명령은
viewer를 자동 실행하지 않아 `VIEWER_PROCESS_E2E_NOT_RUN`을 유지한다.

단위 테스트와 smoke test는 현재 코드의 계약을 검증한다. 실제 사용자 환경의 wake word 정확도, 장기 작업 성공률, UI 변화 복구율 또는 안전성 우월성을 증명하지 않는다. `evaluation/intent_scenarios.json`의 100% 결과도 수작업으로 라벨링된 evidence에 대한 정책 일치도이며 실제 음성 분류기 성능이 아니다.

## 다음 구현 순서

1. 사용자가 화면 앞에 있을 때 Hello `VERIFIED` 성공 경로 확인 및 배포 인증서 서명
2. 사용자 동의 후 실제 마이크 device smoke test와 실제 한국어 corpus 평가
3. 브라우저 등 추가 앱의 제한된 변경 adapter와 선택적 Vision fallback
4. snapshot VM 위험 action·복구 시나리오 실행
5. 실제 참가자 실험 결과 수집

snapshot VM evidence gate는 hypervisor를 제어하거나 host에서 위험 action을 실행하지 않는다.
외부 harness가 생성한 문서만 검증하며, 정확한 manifest binding, UTC freshness, 최대 실행 시간,
복원 성공, 예상 결과, 주입된 signature verifier가 모두 통과해야 결과를 수락한다. 이 구현은
실제 VM 격리나 snapshot 복구 성공을 자체적으로 증명하지 않으므로 외부 harness 연동 완료를
주장하지 않는다. 상세 계약은 `docs/SNAPSHOT_VM_EVIDENCE.md`에 있다.

음성 의존성은 `pip install -e .[voice]`로 분리했다. adapter는 런타임에 모델을 내려받지
않으며 `from_local_model`에 기존 로컬 파일/디렉터리를 넘겨야 한다. 현재 CI는 dependency를
주입한 계약 테스트만 수행했으며 실제 openWakeWord/faster-whisper binary와 한국어 모델의
정확도·지연시간을 검증한 결과가 아니다.

Windows 환경에서는 SciPy 1.18.1의 unsigned `_tools.pyd`가 이 PC의 application-control
정책에 차단되어 voice extra를 검증된 `scipy>=1.16,<1.17`로 제한했다. 또한 openWakeWord
0.6.0이 끌어온 scikit-learn 1.9.1 native module이 같은 정책에 차단돼
`openwakeword>=0.5.1,<0.6`, `scikit-learn>=1.7,<1.8`로 고정했다. SciPy 1.16.3과
scikit-learn 1.7.2에서 openWakeWord import와 ONNX runtime이 성공했다. 공식 openWakeWord v0.5.1 release의
`hey_jarvis_v0.1.onnx`, `melspectrogram.onnx`, `embedding_model.onnx`를 프로젝트 local bundle로
저장하고 manifest SHA-256 검증 후 80 ms 무음 frame inference(score 0.0)를 수행했다. 이 smoke는
실제 발화의 wake 정확도·false activation rate를 증명하지 않는다.

offline corpus 하네스는 모든 WAV의 경로·symlink·SHA-256·mono 16 kHz signed 16-bit PCM·크기·
길이를 먼저 검증한 뒤에만 injected STT를 호출한다. 보고서에는 transcript 원문이나 파일 경로를
남기지 않는다. 실제 음성 수집은 자동으로 수행하지 않았으며, 사용자·참가자 동의와 보관 정책이
준비된 뒤 실행해야 한다. metric 계약은 `docs/VOICE_EVALUATION.md`에 정리했다.

`Systran/faster-whisper-tiny` commit
`d90ca5fe260221311c53c58e660288d3deb8d356`의 CTranslate2 파일 네 개를 local bundle로 저장했다.
manifest가 source revision, 필수 filename, byte size, SHA-256을 고정하고 초기화 직전에 다시
검증한다. 실제 CPU int8 load는 0.365초, 1초 무음 추론은 0.202초였고 빈 transcript를 반환했다.
이는 한국어 발화 정확도나 실제 장치 latency 결과가 아니다.

로컬 TTS는 `SAPI.SpVoice`를 별도 isolated worker에서 plain text로 호출한다. 자동 검증에서는
스피커 대신 allowed-root 아래 WAV로 렌더링했으며 취소 지원 반영 후 172,424바이트 출력의 SHA-256,
identity와 WAV 구조를 parent가 독립 확인했다. 10ms 후 shared token을 set한 실제 취소 smoke에서는
부분 WAV와 고아 worker가 남지 않았다. 실제 한국어 발음 품질과 스피커 출력은 사용자 참여 없이는
검증하지 않았으며 상세 경계는 `docs/TTS.md`에 있다.

2026-09-21 실제 PC에서 `sounddevice` metadata와 `check_input_settings`만 사용하는 read-only
probe를 실행했다. 28개 endpoint 중 입력 장치 16개, 16 kHz mono int16 호환 9개를 확인했고
기본 입력도 호환됐다. 보고서에는 장치 index와 수치 capability만 포함하며 이름·host API·driver
예외 내용은 제거한다. 이 결과는 stream open, audio capture, 잡음·지연·인식 품질 검증이 아니다.

app registry는 로드 시와 실행 직전에 실행 파일 hash를 각각 확인한다. publisher가 설정된
항목은 Authenticode 상태가 `Valid`이고 인증서 subject가 설정값과 정확히 같아야 한다.
`require_publisher=True` 운영 모드에서는 publisher가 없는 항목도 거부한다. 이 검사는 publisher
신원과 파일 무결성을 확인하지만 애플리케이션 자체가 안전하다는 보증은 아니다.

Windows `process.launch`는 `CreateFileW`의 `GENERIC_READ`, `FILE_SHARE_READ`만으로 실행 파일
handle을 열어 final canonical path와 handle stream SHA-256을 확인하고, handle을 닫기 전에
process creation을 호출한다. 표준 write/delete sharing을 막아 검사 직후 파일 교체 창을 줄인다.
lease 획득·검증·해제 실패는 fail-closed이며 launch 호출 뒤 실패는 자동 재시도하지 않는다.
관리자, kernel component, 이미 열린 호환되지 않는 handle까지 방어한다고 주장하지 않는다.

`ui.element.inspect`는 planner가 지정한 PID를 그대로 신뢰하지 않는다. 성공한
`process.launch` 결과를 process inspector로 확인해 session registry에 등록한 PID만 허용하고,
UIA 접근 직전 실행 이미지 경로, hash, Windows process creation time을 다시 검사한다. 구조적
metadata만 결과로 반환하며 UI element name/value는 반환하지 않는다. creation time이 달라진
PID는 같은 실행 파일이어도 기존 session 권한을 잃는다. task 종료 시 session도 `clear()`한다.

`notepad.text.replace`는 planner가 AutomationId나 ControlType을 지정할 수 없고 trusted
`NotepadProfile`만 사용한다. Windows 11 현재 버전의 profile은 exact `Document` control을 쓰며
`notepad` app ID로 승인된 session인지 확인하고 mutation 직전에 runtime ID를 다시 비교한다.
변경 내용은 로그나 execution observation에 남기지 않고 SHA-256만 반환한다. mutation 호출 이후
예외·timeout·hash mismatch는 상태가 불명확하므로 retry-safe가 아니다. 2026-09-22 새 미저장
Notepad process에서 임시 문자열 replace, 즉시 hash, 독립 predicate 재확인이 모두 성공했으며
파일을 저장하지 않고 해당 새 PID만 종료했다. 실제 사용자 문서는 변경하지 않았다.

Windows 11 Notepad는 `System32\notepad.exe` launcher PID에서 versioned WindowsApps runtime으로
handoff한다. registry는 두 image의 path·hash·publisher를 각각 고정한다. resolver는 launch 전
runtime PID snapshot을 잡고 제한 시간 전체에서 후보를 polling한다. 동일한 새 exact runtime
후보 하나가 끝까지 유지될 때만 PID를 반환하며 후보 소멸·교체·복수 후보를 거부한다. 중간
bootstrap process를 즉시 승인하지 않는다. Windows Update로
version/hash가 바뀌면 자동 탐색하지 않고 registry를 명시적으로 재검토해야 한다.

pywinauto `find_elements`에는 timeout 인자가 없으므로 운영 UIA adapter는 고정된
`python -I -m javis_core.uia_worker` child process에서 strict JSON 요청을 처리한다. parent는 5초
timeout, 200 KiB 요청, 64 KiB 응답, 최소 환경을 적용한다. worker 오류 text와 document 원문은
반환하지 않는다.

`file.delete.trash`는 허용 루트 내부의 일반 파일 하나만 받고 symlink·상대 경로·target
mismatch를 거부한다. 호출 직전에 SHA-256과 canonical path를 다시 검사한다. backend 호출 뒤
예외 또는 파일 잔존은 상태가 불명확하므로 자동 재시도하지 않는다. 프로젝트 내부 일회성
fixture를 Windows 휴지통으로 보내는 실제 smoke test가 성공했으며 영구 삭제는 수행하지 않았다.

감사 로그는 구조적 secret key redaction을 적용하고 메모리 sink는 기본 최대 10,000개 event만
유지한다. JSONL sink는 선택적 `AuditRetentionPolicy`로 파일 크기와 backup 수를 제한하며 회전
뒤 append를 `fsync`한다. audit rotation과 move journal은 별도 `.lock` 파일의 Windows
byte-range lock을 사용해 여러 JAVIS process의 append/load/rotation을 직렬화하며 기본 lock
timeout은 5초다. 데이터 파일과 lock 파일에는 현재 process token의 사용자 SID와 LocalSystem
SID만 full-control ACE로 허용하는 protected DACL을 적용한다. 적용 직후 DACL을 다시 읽어
inheritance 차단, 정확한 SID 집합, ACE type·flags·mask를 검증한다. 소유자와 SACL은 변경하지
않으며 ACL 적용 또는 검증 실패는 fail-closed다.

로컬 승인 창과 Windows Hello native helper를 구현했다. Microsoft 문서상 desktop 앱은
`UserConsentVerifier.RequestVerificationAsync`를 직접 호출하는 대신 활성 application HWND로
`UserConsentVerifierInterop.RequestVerificationForWindowAsync`를 사용해야 한다. helper는
표시되는 자체 WinForms 창의 HWND를 전달하며, 그 전에 `UserConsentVerifier.CheckAvailabilityAsync`
를 확인한다. PIN·생체 정보는 OS UI 밖으로 나오지 않는다.

Python 측 `WindowsHelloHelperVerifier`는 helper의 절대 경로·symlink 여부·SHA-256을 매번
확인하고 random challenge를 stdin JSON으로 전달한다. 요청은 8 KiB, stdout은 4 KiB 상한과
strict schema를 적용하며 같은 challenge의 결과만 받는다. 프로젝트 local .NET SDK 9.0.306으로
`net9.0-windows10.0.22621.0` managed helper를 실제 빌드했다. unsigned single-file apphost는 이
PC의 application-control 정책에 차단되어 사용하지 않는다. 대신 Authenticode `Valid`인 Microsoft
system `dotnet.exe`와 helper DLL·deps·runtimeconfig·WinRT dependency 다섯 파일을 각각 hash로
고정하고, 배포 디렉터리에 미등록 파일이 하나라도 있으면 실행을 거부한다. malformed-input 거부는
실제 hosted helper에서 검증했다. 사용자 참여형 호출은 제한 시간 내 `VERIFIED`를 얻지 못해
`FAILED`로 닫혔다. 따라서 성공 인증 경로와 별도 배포용 Authenticode apphost는 아직 완료로
주장하지 않는다.

## Move journal 보안·복구 한계

이동 journal에는 복구 판정을 위해 원본·목적지의 절대 경로와 SHA-256이 저장되므로
민감한 로컬 상태로 취급해야 한다. POSIX에서는 생성 모드와 `chmod(0600)`을 적용하고,
Windows에서는 journal과 lock 파일에 현재 사용자+LocalSystem protected DACL을 적용해
검증한다. process-local thread lock과 Windows interprocess byte-range lock을 함께 사용한다.
recovery는 파일을 이동하거나 삭제하지 않고, 관찰된 상태에 따라 terminal 레코드만
덧붙인다. 모호한 상태는 `AMBIGUOUS`로 남겨 사용자 확인을 요구한다.

## 2026-10-07 실제 음성 장치 검증

- 고정 한국어 TTS 재생, 사용자 품질 평가 3/5, Windows Hello 실제 인증은 PASS했다. 사용자는 TTS가
  번역기처럼 들려 후속 품질 개선이 필요하다고 평가했다.
- 설치된 SAPI/OneCore 한국어 음성은 Heami 하나뿐임을 확인했다. Kokoro는 한국어 미지원, Piper
  한국어 모델은 비상업 데이터 라이선스로 보류했다. Supertonic 3를 제품 밖에서 CPU 실행해 10화자
  WAV를 생성했고 RTF 0.101~0.123을 확인했지만 자연스러움 증거는 아니므로 기본 TTS를 바꾸지 않았다.
  Heami를 포함한 익명 A~K 청취 세트와 hash manifest를 만들었으며 사용자 평가는 아직 없다.
- 마이크 probe는 28개 endpoint, 입력 장치 16개, 16 kHz mono 호환 장치 9개와 호환 기본 입력을
  확인했다.
- 두 번의 동의 기반 마이크 시험은 실제 frame과 speech energy를 확인했지만, 공식 영어
  `hey_jarvis_v0.1` 모델이 한국어식·영어식 발화를 모두 감지하지 못해 `WAKE_NOT_DETECTED`였다.
- `VoicePipeline`에 원음·문장·장치명을 남기지 않는 frame/speech/max-confidence/wake 상태 진단을
  추가했다. 사용자 검증은 실패를 `AUDIO_NO_FRAMES`, `AUDIO_NO_SPEECH`, `WAKE_NOT_DETECTED`,
  `VOICE_UTTERANCE_INCOMPLETE`로 구분한다.
- 공개 Jarvis 세 구현은 Google STT 결과 문자열 검사 방식이라 현재 로컬 관문보다 개인정보·오프라인·
  오작동 경계가 약했다. 복사하지 않고 한국어 맞춤 ONNX 모델을 FAR/FRR로 평가하기로 했다.
- 전체 회귀는 766 passed, 3 skipped, Ruff clean, mypy strict 123 source files다.
- 후속 wake-word corpus 하네스와 CLI를 추가한 뒤 전체 회귀는 783 passed, 3 skipped,
  Ruff clean, mypy strict 124 source files다. 실제 동의 corpus가 없으므로 FAR/FRR 측정값은 아직 없다.
- `wakeword-forge`와 공개 `okay-hermes` ONNX를 감사했다. 실제 그래프는 가변 시간축
  `float32[batch,time]` 입력이지만 3초 창을 권장하고 현재 openWakeWord와 계약이 달라 직접 교체하지
  않았다. 고정 SHA-256·크기·입출력 검증, 3초 ring buffer, 400 ms hop과 reset을 갖춘 실험용
  `RepCnnWakeWordDetector`를 추가했다. 공개 예제 무음 smoke는 점수 `0.624035120`을 반환했으므로
  0.5 하한만으로 모델을 채택할 수 없고 실제 동의 corpus의 모델별 FAR/FRR 보정이 필요하다.
- 별도 `evaluate-repcnn-wakeword-corpus` CLI도 추가해 모델 경로와 64자리 SHA-256을 명시한 경우에만
  후보 ONNX를 같은 FAR/FRR 하네스로 평가할 수 있다. 제품 기본 모델은 바뀌지 않았다.
- 합성 3초 무음 목표 표시·비호출어 표본 각 1개와 공개 예제 모델로 CLI 전체 경로를 실행했다.
  임계값 0.7에서 inference failure 0, FAR 0.0, FRR 1.0이 나왔으며 두 표본의 최대 점수는
  `0.624035120`이었다. 이는
  연결 검증 결과이고 음성이 없는 양성 표본이므로 성능 자료로 사용하지 않는다.
- 이 단계 구현 뒤 최종 전체 회귀는 상단 최신 재검증처럼 797 passed, 3 skipped다.
- 동의 기반 호출어 자료 수집 핵심부와 `capture-wakeword-corpus` CLI를 추가했다. 매 실행 새
  `동의 <무작위 토큰>`과 표본별 `준비`를 요구하고, 새 절대 출력 폴더만 허용하며, 원음을 쓰기 전
  빈 파일부터 사용자 전용 ACL을 적용한다. WAV/manifest 해시와 동의 기록을 결합하고 중간 실패 시
  이번 실행이 만든 파일만 정리한다. 비대화형 안전 시험은 `CONSENT_NOT_GIVEN`, 마이크 source 0회,
  출력 폴더 0개였으며 실제 녹음은 수행하지 않았다. 최신 전체 회귀는 809 passed, 3 skipped다.
- 호출어 자료 형식 1.1에 거리·소음·비식별 세션 조건을 추가했다. 각 조건 조합에 양성과 비호출어가
  모두 없으면 거부하고, 조건별 FAR/FRR의 confusion count와 실패 수 합계가 전체와 일치해야 한다.
  추론 실패는 조건 보고서에서도 FN/FP로 남는다. 구형 1.0 자료는 미지정 조건으로 호환되며 최신
  전체 회귀는 814 passed, 3 skipped다.
- 별도 보정 corpus의 최대 점수로 0.50~0.99 임계값을 한 번에 비교하는 두 calibration CLI를
  추가했다. FAR 상한을 만족한 후보 중 FRR·FAR·높은 임계값 순으로 선택하고, 후보가 없으면 실패로
  닫힌다. 합성 무음 양성·비호출어 각 1개에서 공식 영어 모델은 모든 후보 FAR 0/FRR 1이라 0.99가
  기계적으로 선택됐지만, 호출어가 없는 양성 표본이므로 품질 근거가 아니다. 최신 전체 회귀는
  824 passed, 3 skipped다.
- 사용자 동의 아래 0.5 m 조용함과 3~5 m 사람 말소리 조건에서 양성·비호출어 각 1개, 총 4개를
  실제 수집했다. WAV 형식·길이·hash·protected ACL·nonzero 신호·clipping 0을 확인했다. 공식 영어
  모델의 임계값 0.5 결과는 TP 0/TN 2/FP 0/FN 2, FAR 0.0/FRR 1.0이었다. 양성 최대 점수는
  `0.396607757`, `0.068618000`으로 허용 임계값 하한 0.5보다 낮았다. 이 결과로 실제 수집 경로는
  검증됐지만 현재 모델은 이번 한국어 pilot 조건에서 실사용 기준을 충족하지 못했으며, 4표본 결과를
  일반적인 성능 결론으로 확대하지 않는다.
- 실제 수집 중 CLI가 계획의 거리·소음·세션 조건을 표시하지 않는 운용 결함을 발견했다. readiness
  화면이 기존 enum을 고정 한국어 문구로 표시하도록 수정했고, 미지정·근거리 조용함·원거리 말소리
  조건과 기존 exact `준비` 계약을 회귀시험으로 고정했다. 최종 전체 회귀는 826 passed,
  3 skipped다.
- 공식 한국어 `vosk-model-small-ko-0.22`를 제품 밖에서 시험했다. 일반 인식·원음 조합은 실제
  4표본 pilot에서 TP 2/TN 2/FP 0/FN 0이었지만, 제한 문법은 비호출어 1개를 오탐했고 음량 증폭은
  원거리 양성 1개를 놓쳤다. 일반 인식만 쓰는 실험 어댑터, 3.2초 확정·0.8초 겹침 창, 전사문
  비노출, 정확한 15파일 manifest 검증과 별도 평가 CLI를 추가했다. 첫 검출 상한은 2,080 ms와
  3,200 ms였다. 4표본은 성능 근거가 아니며 264,364,726바이트의 풀린 모델과 선택 의존성은 기본
  제품·설치본에 넣지 않았다. 전체 회귀는 843 passed, 3 skipped, coverage 83%, Ruff clean,
  mypy strict 128 source files다.
- 최신 wheel SHA-256은 `dd7012317e3667fb6caa9f02eecbbfd3eba9b39f371f393938b1e37562efffe6`,
  one-folder manifest SHA-256은 `ab95beca300eaf90b43a4e940dcbf588d4f16917a28fc47b36a6940efb56c087`,
  Setup SHA-256은 `cb4edd33413732c7f69fc4662c848f22c1fcee7decb2d1caeed65bfd3fd428c5`다.
  기본 배포물의 Vosk 모델/native 파일은 0건이며 서명은 아직 `NotSigned`다.
- 4표본 pilot과 섞이지 않는 보정 세션 16표본·최종시험 세션 16표본 계획을 추가했다. 각 계획은
  근거리/원거리×조용함/사람 말소리 네 조건에 양성·평범한 음성·이름만 말하는 어려운 음성을
  균형 배치하며, 서로 다른 새 동의와 새 출력 폴더가 필요하다. 아직 녹음은 시작하지 않았다.
