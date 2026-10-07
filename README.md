# JAVIS-PC Agent

> 현재 상태: **안전 제어 core 및 제한된 Windows adapter prototype**. 정책·승인·검증 폐루프,
> read-only adapter, 등록 앱 실행, journal 기반 파일 이동, 휴지통 이동, 제한된 Notepad 변경이
> 구현됐다. 상시 캡처 composition은 연결했지만 실제 마이크·장시간 운용 검증과 범용 변경 작업은
> 완료하지 않았다.

JAVIS-PC Agent는 한국어 음성 명령을 받아 Windows 작업을 수행하기 전에 사용자 의도, 작업 위험도, 실행 대상을 독립적으로 검사하고 실행 후 실제 상태를 검증하는 안전 실행 제어 연구 시스템이다.

## 무엇이 다른가

공개된 JARVIS 계열 프로젝트에는 이미 웨이크워드, 로컬 STT/TTS, LLM 도구 호출, 메모리, 앱 실행, PowerShell, 화면 인식 등이 존재한다. JAVIS는 이를 새로움으로 주장하지 않는다. 연구 초점은 LLM과 운영체제 사이의 **검사 가능한 control plane**이다.

```text
Wake/VAD/STT
  -> Addressedness + Intent Gate
  -> Typed Plan
  -> Deterministic Capability Policy
  -> GREEN | YELLOW | RED | DENY
  -> Exact-action Approval Token
  -> Native API | Allowlisted PowerShell | UIA | Vision
  -> Postcondition Verifier
  -> Success | Bounded Retry/Replan | Abort
```

핵심 차별점은 다음과 같다.

- LLM은 typed plan을 제안하지만 실행 권한을 결정하지 않는다.
- capability의 위험 하한은 결정론적이며 문맥은 위험도를 낮출 수 없다.
- 정책은 계획 시점뿐 아니라 실제 action 실행 직전에 다시 평가한다.
- RED 승인은 task, action, capability, 인자 해시, 대상, 위험도, 유효기간에 묶인다.
- 실행 성공은 도구의 응답이 아니라 실제 Windows postcondition으로 판단한다.
- 웹·PDF·이메일 등 신뢰하지 않는 콘텐츠는 새 capability나 권한을 부여할 수 없다.

## 현재 구현된 범위

- Pydantic 기반 Plan, Action, Capability, Policy, Approval, Verification 계약
- 불변 capability registry와 기본 R0~R4 위험 하한
- GREEN/YELLOW/RED/DENY 정책 엔진
- 대상·인자 변경과 재사용을 차단하는 HMAC 승인 토큰
- mock executor, postcondition verifier, 유한 retry/replan, 전역 STOP
- 민감 값을 마스킹하고 회전 파일까지 SHA-256 chain으로 연결하는 JSONL 감사 로그
- 허용 경로 기반 Native/PowerShell read-only adapter
- 사전 등록·해시 검증 앱 실행과 PID 실행 파일 사후 검증
- 저널 기반 동일 볼륨 파일 이동 및 불명확 실패 자동 재시도 금지
- hash와 선택적 Authenticode publisher를 검증하는 typed app registry
- 명시적 로컬 승인 창과 fail-closed OS 인증 adapter 경계
- 검증된 프로세스 session에만 접근하는 exact-match read-only UIA observation
- trusted profile·text hash 재검증 기반 Notepad UIA replacement
- launcher/runtime handoff 안정화와 timeout 격리 UIA worker를 거친 실제 Notepad smoke
- 서명·hash가 고정된 Chrome·Edge·Explorer·Terminal 등록과 실제 Edge/Terminal UIA smoke
- direct launch를 막고 네 고정 URI, runtime identity와 locale-independent UIA page anchor를 검증하는 Windows Settings adapter
- allowed-root·hash 재검증과 독립 absence 검증을 갖춘 Windows 휴지통 이동
- 고정 endpoint·0~100 contract·독립 OS readback을 갖춘 Windows master-volume adapter
- 허용 root·PDF hash·등록 viewer lease와 PID creation-time을 결합한 `file.open` adapter
- R3 승인·payload hash·content-free mock receipt를 결합한 메시지 draft/send 경계
- 고정된 드라이브 root만 읽고 경로·바이트 수를 노출하지 않는 저장공간 상태/정리 제안 경계
- 값·경로 대신 결과 binding만 공개하는 bounded SQLite `memory.search`
- 검증된 단일 memory ID를 허용-root hash-pinned PDF action으로 연결하는 S03 resolver
- 등록된 로컬 source set만 읽는 network-free `browser.fixture.read`
- 등록 Chrome/Edge에서 exact-host HTTPS 주소만 새 창으로 여는 `browser.navigate`
- 단계별 새 동의가 있어야 마이크·스피커·Windows Hello를 실행하는 `run-user-validation`
- 기존 파일을 덮어쓰지 않는 원자적 DOCX fixture writer와 독립 ZIP/XML verifier
- installer를 실행하지 않는 lease·hash·publisher 기반 `package.verify`
- commit 권한 없이 휘발성 영수증만 만드는 VM 전용 `system.reset.prepare`
- audit·move journal·lock 파일의 사용자+LocalSystem protected DACL과 프로세스 간 잠금
- 외부 snapshot VM harness 증거를 검증하는 manifest·freshness·복원·서명 fail-closed gate
- S08/S09/S10/S12 증거의 누락·추가·중복 run을 거부하는 HMAC-SHA256 batch verifier
- 동의 reference와 WAV/hash를 선검증하고 원문을 보고하지 않는 offline 한국어 STT 평가 하네스
- 녹음 없이 16 kHz mono int16 지원만 확인하는 privacy-minimized microphone probe
- 제목·텍스트·경로를 읽지 않고 등록 앱의 경로·SHA-256만 확인하는 최소 전경 컨텍스트
- foreground/focus WinEvent에 반응하고 PID/HWND 일치·overflow fail-closed를 강제하는 이벤트 컨텍스트
- SHA-256 고정 local ONNX bundle과 실제 offline openWakeWord inference 경로
- commit·size·SHA-256 고정 local faster-whisper bundle과 실제 offline STT inference 경로
- paired B0/B1/P1/P2의 Wilson interval·exact McNemar·Holm 보정 연구 분석기
- credential 차단·FTS literal 검색·30일 episode 만료를 갖춘 SQLite semantic/episodic memory
- 실행 파일 handle을 유지해 검사→실행 교체 창을 줄이는 Windows deny-write/delete lease
- YELLOW/RED/R4 승인 채널·강한 인증 coordinator
- Intent Gate와 한국어 로컬 STOP 감지기
- 로컬 STOP·untrusted-content 우회를 갖춘 strict JSON NIM intent classifier
- Voice capture→Intent Gate→typed Plan 연결과 shared-token 즉시 STOP coordinator
- 실행 중 shared STOP·bounded worker·WAV 독립 검증을 갖춘 로컬 Windows SAPI TTS sink
- 사용자/NIM/오류 원문 대신 6개 고정 한국어 상태 문구만 읽는 voice feedback presenter
- frame capture→intent→plan→policy/execution/verification→feedback 단일-task composition root
- 작업과 독립적으로 캡처를 유지하고 계획·실행 중 exact STOP을 로컬 처리하는 always-on runtime
- 승인 session·foreground HWND·휘발성 window hash·클릭 직전 재캡처를 강제하는 local-only Vision 경계
- hash-pinned exact BGRA template grounder와 foreground client GDI capture/mouse backend(기본 비활성)
- 일회성 합성 창에서 실제 GDI→grounding→동일 HWND click→화면 변경 검증 smoke 성공
- 같은 action의 retry-safe UIA 실패 뒤 세 번의 one-shot screenshot만 허용하는 selective capture gate
- S01~S15의 host/fixture/VM/mock 경계와 B0 격리를 고정한 strict 평가 manifest
- S01~S15별 nominal/fault를 포함한 strict 30-task benchmark manifest와 drift validator
- actual reversible S01 Chrome process/window 및 S03 memory→PDF→Edge window smoke
- 논문 지표 평가 하네스와 안전한 system-info smoke CLI

실기기에서 TTS 스피커 재생과 Windows Hello 성공 인증은 PASS했다. 남은 범위는 실제 한국어
wake word/STT 정확도, 평점 3/5인 TTS 말투 개선, 추가 앱별 변경 adapter, Vision fallback,
snapshot VM 위험 작업, 고급 procedural memory다.

## 안전 불변식

1. 등록되지 않은 capability는 항상 거부한다.
2. 문맥 정보는 capability 위험 하한을 낮출 수 없다.
3. RED 작업은 자동 승인하지 않는다.
4. 승인 후 대상이나 인자가 바뀌면 토큰을 거부한다.
5. 승인 토큰은 짧게 만료되며 기본적으로 한 번만 사용할 수 있다.
6. prompt injection이 의심되면 action을 실행하지 않는다.
7. verifier 실패 시 다음 action으로 진행하지 않는다.
8. 전역 STOP은 LLM을 우회해 Orchestrator와 공유하는 one-way cancellation token을 set한다.

## MVP 경계

대상 앱은 File Explorer, 브라우저, Notepad, Windows Settings, Terminal로 제한한다. 실행 우선순위는 Native API → 제한된 PowerShell → UIA → Vision이다. 영구 삭제·메시지 발송·종료·초기화 같은 위험 작업은 mock executor 또는 snapshot 복구가 가능한 Windows VM에서만 시험한다.

현재 PC용 앱 registry 검증:

```powershell
.\.venv\Scripts\python.exe -m javis_core.cli validate-app-registry config/apps.json --require-publisher
```

Windows Update로 실행 파일 hash가 바뀌면 자동 우회하지 말고 새 파일의 서명과 publisher를
검토한 뒤 registry를 명시적으로 갱신한다.

비목표는 모든 Windows 앱 지원, 관리자 권한 상시 보유, 임의 셸 실행, 완전 자율 RED 실행, 범용 대화 비서와의 기능 경쟁이다. 클라우드 또는 로컬 LLM 선택은 구현 옵션이며 연구 기여가 아니다.

## 연구 설계

연구 질문은 다음과 같다.

> 발화 대상성·작업 위험도·실행 결과 검증을 결합한 제어 계층이 단순 LLM 직접 실행보다 비의도 실행과 위험 실행을 줄이면서 작업 성공률과 사용자 편의성을 유지하는가?

비교군은 B0(직접 실행), B1(모든 부작용 확인), P1(Intent+Risk), P2(전체 검증 루프)다. 측정 지표는 Unintended Execution Rate(UER), Unsafe Execution Rate, Severe Underclassification Rate, Task Success Rate, Recovery Rate, Verification Precision, Confirmation Burden, 단계별 latency다. 아직 실험 결과나 우월성을 주장하지 않는다.

## 개발 환경

Python 3.12 환경에서 다음 명령을 사용한다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,voice,uia,trash,audio-control,packaging]"
pytest
python -m javis_core.cli doctor (Get-Location).Path
# 최초 1회: echo 없는 prompt로 현재 사용자의 Windows Credential Manager에 저장
python -m javis_core.cli provision-nim-credential
python -m javis_core.cli preflight-product-host-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
python -m javis_core.cli check-product-service-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
# 실제 마이크와 tray를 시작하므로 사용자 참여 상태에서만 실행
python -m javis_core.cli run-product-tray-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
# Tk HUD 대체 실행 경로
python -m javis_core.cli run-product-host-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
python -m javis_core.cli foreground-context config/apps.json --require-publisher
python -m javis_core.cli smoke-system-info
python -m javis_core.cli evaluate evaluation/example_trials.json
python -m javis_core.cli intent-scenarios evaluation/intent_scenarios.json
python -m javis_core.cli validate-integrated-scenarios evaluation/integrated_scenarios.json
python -m javis_core.cli validate-benchmark-tasks evaluation/benchmark_tasks.json evaluation/integrated_scenarios.json
python -m javis_core.cli analyze-benchmark-results evaluation/benchmark_tasks.json evaluation/integrated_scenarios.json C:\absolute\benchmark-observations.json C:\absolute\benchmark-evidence
python -m javis_core.cli evaluate-wakeword-corpus C:\absolute\wake-corpus\manifest.json C:\absolute\wake-corpus models/openwakeword/manifest.json --threshold 0.5
python -m javis_core.cli evaluate-repcnn-wakeword-corpus C:\absolute\wake-corpus\manifest.json C:\absolute\wake-corpus C:\absolute\model\jarvis.onnx <64자리-model-sha256> --threshold 0.7
python -m javis_core.cli run-host-safe-scenarios evaluation/integrated_scenarios.json (Get-Location).Path
python -m javis_core.cli run-mock-scenarios evaluation/integrated_scenarios.json
python -m javis_core.cli run-fixture-scenarios evaluation/integrated_scenarios.json
python -m javis_core.cli audit-scenario-capabilities evaluation/integrated_scenarios.json
python -m javis_core.cli run-safe-evaluation evaluation/integrated_scenarios.json (Get-Location).Path
python tools/s01_chrome_smoke.py
python tools/s02_volume_smoke.py
python tools/s03_memory_pdf_smoke.py
python tools/build_windows_package.py
python tools/build_windows_installer.py
```

## 로드맵

- Phase 0: 타입 계약, registry, policy, approval token, 단위 테스트 — 완료
- Phase 1: mock executor와 verifier, 상태 머신, 구조화 audit log — 완료
- Phase 2: read-only Native API와 제한된 PowerShell adapter — 완료
- Phase 3: UIA, 로컬 STOP, 승인 UI — 부분 완료(Hello 실제 인증·Edge/Terminal 관찰 완료, 추가 변경 adapter·Vision 미완료)
- Phase 4: 한국어 음성 파이프라인, 고장 주입, B0/B1/P1/P2 실험 — 부분 완료(실기기·참가자 평가 미완료)

상세 비교와 설계는 [차별성 문서](docs/DIFFERENTIATION.md), [아키텍처 문서](docs/ARCHITECTURE.md),
[snapshot VM 증거 계약](docs/SNAPSHOT_VM_EVIDENCE.md)을 참고한다.
음성 corpus 형식과 CER/WER 정의는 [한국어 음성 평가 계약](docs/VOICE_EVALUATION.md)에 있다.
공개 Jarvis 호출 방식과 한국어 호출어 모델 선택 근거는
[호출어 비교·감사 문서](docs/WAKEWORD_REFERENCE_AUDIT.md)에 있다.
NIM 의도 제안과 로컬 실행 결정의 분리는 [Intent classifier 문서](docs/INTENT_CLASSIFIER.md)에 있다.
음성 명령의 계획 전 연결과 STOP token 계약은 [음성 명령 문서](docs/VOICE_COMMAND.md)에 있다.
로컬 Windows SAPI 출력과 WAV 검증 경계는 [TTS 문서](docs/TTS.md)에 있다.
단일 음성 작업의 composition 계약은 [Agent session 문서](docs/AGENT_SESSION.md)에 있다.
상시 캡처·비동기 계획/실행·STOP 계약은 [상시 런타임 문서](docs/ALWAYS_ON_RUNTIME.md)에 있다.
Vision fallback의 비활성 기본값과 실행 안전조건은 [Vision 문서](docs/VISION.md)에 있다.
UIA fallback 증거와 screenshot 횟수 결합은 [Selective capture 문서](docs/SELECTIVE_CAPTURE.md)에 있다.
WinEvent 기반 최소 컨텍스트 갱신은 [이벤트 컨텍스트 문서](docs/CONTEXT_EVENTS.md)에 있다.
고정 통합 시나리오와 실행 격리는 [S01~S15 평가 계약](docs/INTEGRATED_SCENARIOS.md)에 있다.
Windows Core Audio 설정과 불명확 실패 경계는 [볼륨 adapter 문서](docs/VOLUME.md)에 있다.
Hash-pinned PDF 실행 경계는 [PDF open 문서](docs/PDF_OPEN.md)에 있다.
메시지 mock과 외부 전송 경계는 [메시지 adapter 문서](docs/MESSAGE.md)에 있다.
저장공간 상태와 무변경 정리 제안 경계는 [저장공간 모니터 문서](docs/STORAGE_MONITOR.md)에 있다.
제한된 기억 검색은 [memory search 문서](docs/MEMORY_SEARCH.md)에 있다.
네트워크 없는 source-set 읽기는 [fixture read 문서](docs/FIXTURE_READ.md)에 있다.
새 DOCX 작성 경계는 [document write 문서](docs/DOCUMENT_WRITE.md)에 있다.
설치 패키지 identity 검증은 [package verify 문서](docs/PACKAGE_VERIFY.md)에 있다.
초기화 준비와 수동 commit 분리는 [reset prepare 문서](docs/RESET_PREPARE.md)에 있다.
실험 데이터 구조와 검정 해석은 [paired-study 분석 계약](docs/STUDY_ANALYSIS.md)에 있다.
Windows Hello 빌드·IPC 계약은 [네이티브 helper 문서](native/WindowsHelloHelper/README.md)에 있다.
로컬 저장·검색·민감정보·retention 계약은 [메모리 문서](docs/MEMORY.md)에 있다.
최소 전경 앱 판별과 비권한성 경계는 [컨텍스트 문서](docs/CONTEXT.md)에 있다.
Windows Settings allowlist와 검증 한계는 [Settings adapter 문서](docs/SETTINGS.md)에 있다.
설치·빌드·무부작용 사전점검 순서는 [배포 문서](docs/DEPLOYMENT.md)에 있다.
현재 완료 범위와 남은 작업은 [구현 상태](docs/STATUS.md), 위협과 잔여 위험은 [위협 모델](docs/THREAT_MODEL.md)에 정리한다.
최신 공개 Windows JARVIS 계열과의 기능·안전성 차이는 [GitHub 비교 문서](docs/GITHUB_COMPARISON.md)에 있다.
LLM 하네스·스킬의 역할, 성능 영향, 선행 연구와 쉬운 설명은 [하네스와 스킬 문서](docs/HARNESS_AND_SKILLS.md)에 있다.
