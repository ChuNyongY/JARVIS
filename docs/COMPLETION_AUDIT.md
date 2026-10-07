# 원 계획서 대비 완료 감사

최종 갱신: 2026-10-07

## 2026-10-07 음성·사용자 검증 갱신

- 전체 회귀는 **829 collected, 826 passed, 3 skipped**, line coverage 83%, Ruff clean,
  mypy strict 126 source files다.
  소스 실행 기준 doctor 15/15, Credential preflight 3/3, 13-capability service `CREATED`와
  `capture_running=false`를 재확인했다.
- 실제 기본 마이크에서 frame과 음성 energy가 들어오는 것은 확인했지만 공식 영어
  `hey_jarvis_v0.1`은 한국어식·영어식 발화를 모두 감지하지 못했다. 실패 지점은 마이크나 STT보다
  호출어 모델이다.
- 동의·SHA-256이 결합된 로컬 WAV만 읽는 FAR/FRR 평가기와 CLI를 추가했다. 추론 실패를 분모에서
  빼지 않고 양성 표본은 FN, 비호출어 표본은 FP로 보수적으로 계산한다.
- 자료 형식 1.1은 거리·소음·비식별 세션 조건을 기록하고 각 조건의 양성·비호출어 균형을 강제하며,
  전체 수치와 같은 실패 산입 원칙으로 조건별 FAR/FRR을 낸다. 1.0 자료는 미지정 조건으로 읽는다.
- 별도 보정 corpus에서 FAR 상한을 먼저 만족시키고 FRR·FAR·높은 임계값 순으로 0.50~0.99 중 하나를
  고정하는 offline calibration CLI를 추가했다. 가능한 후보가 없으면 임계값을 만들지 않는다.
- 사용자 동의 아래 실제 4표본 pilot을 수집했다. WAV 형식·길이·hash·protected ACL·nonzero
  신호·clipping 0이 모두 확인됐지만, 공식 영어 모델의 임계값 0.5 결과는 TP 0/TN 2/FP 0/FN 2,
  FAR 0.0/FRR 1.0이었다. 따라서 실제 수집·평가 경로는 직접 입증됐지만 현재 모델은 이번 pilot
  조건에서 기준 미달이다. 표본 수가 너무 작으므로 논문 성능 결론에는 쓰지 않는다.
- `wakeword-forge` 계열 후보용 RepCNN 어댑터와 별도 평가 CLI를 추가했다. 해시·크기·ONNX 입출력,
  3초 창과 400 ms hop을 검증하지만 제품 기본 경로에는 연결하지 않았다. 공개 `okay-hermes`의 무음
  점수 `0.624035120`과 합성 CLI FAR 0/FRR 1은 연결 증거일 뿐 한국어 성능 근거가 아니다.
- 실제 고정 한국어 TTS 스피커 재생과 Windows Hello `VERIFIED`는 PASS했다. TTS 사용자 평가는
  3/5이며 번역기 같은 말투를 개선해야 한다. 실제 동의 한국어 corpus와 맞춤 `Jarvis` 모델은 없다.

## 2026-10-06 재검증

- Chrome 자동 업데이트로 등록 SHA-256이 달라진 상태를 발견해 Google Authenticode 발행자를 다시 확인하고 `config/apps.json`을 현재 해시로 갱신했다.
- 최신 Chrome은 인자 없는 재실행에서 새 창을 만들지 않을 수 있으므로, 앱 등록부가 허용하는 고정 `--new-window` 하나만 `process.launch`가 전달하도록 계약을 확장했다. 임의 LLM 인자는 계속 거부한다. 실제 S01에서 새 PID/HWND 확인과 정확한 창 정리가 PASS했고, Explorer와 S03 PDF→Edge 가역 시험도 PASS했다.
- 전체 회귀는 **766 passed, 3 skipped, line coverage 83%**, Ruff clean, mypy strict 123 source files다. 소스 실행 기준 doctor 15/15, Credential `PRESENT`, preflight 3/3, 13-capability service `CREATED`를 재확인했다.
- pywin32가 frozen 환경에서 예상 밖 예외를 반환할 때 고정 target을 Windows `CredReadW`로 읽는 제한된 대체 경로를 추가했다. 실제 frozen `JAVIS.exe`에서 Credential `PRESENT`, preflight 3/3, 13-capability service `CREATED`, capture 미실행을 직접 재현했다.
- 새 wheel SHA-256은 `86256abc55e715f9d6748bb3dd385030f95d989a8e599d99973bcee3f4a937c9`다. one-folder manifest 1,826개 항목은 exact verify를 통과했고 manifest SHA-256은 `4127ddbac7d2fee49d6d392031c4f7dac8766d343820d9ec1931ef31ea402abc`다. Setup SHA-256은 `3690062f16e7172fb96e372f603d736f2e2fe81323f3cbb55176070b17c70c22`이며 sidecar와 일치한다. console/tray/Setup은 모두 `NotSigned`다.
- PowerShell 스크립트가 시스템 정책에 차단되는데도 IExpress가 0을 반환하는 설치 결함을 발견했다. 정책 우회 없이 Windows `cmd`·`certutil`·`tar`와 staged `JAVIS.exe verify-package`를 사용하는 설치 진입부로 교체했다. 깨끗한 `%LOCALAPPDATA%\JAVIS-PC\0.1.0`에 실제 Setup 최초 설치 PASS, 11.5초 내 정상 종료, 설치 manifest와 source manifest 일치, Credential preflight 3/3, 13-capability service `CREATED`, autostart `ABSENT`를 확인했다.
- benchmark ledger는 completed 70, ready 0, snapshot VM environment unavailable 50, runner unavailable 0으로 재검증됐다. safe evaluation은 8 PASS / 7 NOT_RUN이다.
- snapshot VM 전용 exact-cell 요청 50개를 `build/snapshot-vm-handoff-20261006`에 내보냈고 고유 cell ID 50개, 민감정보 표식 0건, 요청 SHA-256 `e2429e43354a4225596f0c805d37b1a4d6d246410b7754116fbff9fb0f145769`을 확인했다. 참가자 연구는 `docs/PARTICIPANT_STUDY_PROTOCOL.md`에 동의·조건 균형화·최소 수집·중단 기준을 고정했지만 실제 참가자 데이터는 아직 없다.
- 이 PC에는 Hyper-V·VirtualBox·VMware runner와 신뢰된 코드 서명 인증서가 없다. 따라서 snapshot VM 50셀, Authenticode 서명, 새 동의가 필요한 실제 마이크·스피커·Windows Hello·참가자 연구는 완료 증거가 아니다.

이 문서는 테스트 개수나 모듈 존재를 전체 완성의 대리 지표로 사용하지 않는다. 원 계획서의
명시 요구마다 실제 코드, 실제 Windows smoke, 연구 데이터 중 어떤 증거가 있는지를 구분한다.

상태 정의:

- **완료**: 현재 코드와 직접 검증 증거가 요구 범위를 충족한다.
- **부분**: 계약 또는 제한된 adapter는 있으나 원 요구의 실행 범위 전체를 입증하지 못한다.
- **미검증**: 구현은 있으나 사람·장치·외부 환경이 필요한 직접 증거가 없다.
- **미구현**: 요구된 실행 경로 자체가 없다.

## P0 시스템 요구

| 원 계획 요구 | 상태 | 현재 직접 증거 | 완료에 부족한 증거 |
|---|---|---|---|
| Wake word → VAD → local STT | 부분 | strict local bundle, 실제 4표본 동의 pilot의 WAV/hash/ACL 검증, 영어 모델 실측 TP 0/TN 2/FP 0/FN 2·FAR 0/FRR 1, 동의·hash 결합 FAR/FRR CLI, pinned RepCNN 후보 어댑터, 실제 ONNX/Whisper inference | 더 큰 독립 보정·최종시험 corpus, 기준을 통과한 `자비스` 맞춤 모델과 실제 FAR/FRR·CER/WER |
| Intent/addressedness gate | 부분 | 로컬 STOP, strict NIM proposal, 15개 정책 계약, command/quote/negation/hypothetical 차단 | 실제 한국어 corpus Macro-F1·class precision/recall·calibration |
| 기본 Context | 부분 | exact path/hash foreground app, 안정 표본, 제품 서비스에 연결된 privacy-minimized Planner context(`foreground_app_id`, `foreground_identity_verified`만 노출), foreground/focus WinEvent source, exact event snapshot binding, overflow fail-closed, UIA failure-bound three-purpose selective capture gate | 실제 사용자 앱 장시간 event 운용과 task/session 종합 context |
| Typed Planner | 부분 | strict JSON, duplicate/NaN/size/schema/capability 검증, 사용자 goal 강제 결합 | 현재 NIM endpoint의 반복 timeout 해소와 실제 task-set planning 성공률 |
| Capability Registry | 완료 | immutable registry, risk floor, executor allowlist, typed argument/postcondition contract | 새 capability 추가 시 동일 계약 유지 |
| GREEN/YELLOW/RED 정책 | 완료 | context가 risk를 낮출 수 없는 deterministic policy와 per-action 재검사 | 실제 participant confirmation burden 결과는 연구 항목 |
| Approval UI | 부분 | fail-closed local UI, exact-action token, R4 strong-auth coordinator | 실제 사용자 UI usability, timeout/deny 현장 시험 |
| Windows Hello | 부분 | HWND interop helper build·manifest/hash IPC 검증과 사용자 참여 `VERIFIED` PASS | 별도 배포용 apphost 코드 서명 |
| Native/API execution | 부분 | system info, file list, process launch, Settings URI, file move/trash, bounded volume actual smoke, memory→hash-pinned PDF→Edge window actual fixture smoke, R3 message mock/receipt | install, 실제 message backend와 S02/S03/S06 반복 평가 |
| Restricted PowerShell | 완료(현재 범위) | 고정 read-only script만 허용, 임의 shell/인자 실행 없음 | 새 PowerShell capability마다 별도 고정 script와 검증 필요 |
| UI Automation | 부분 | exact selector observation, authorized session, 실제 Notepad replace/hash | 추가 앱 변경 adapter와 UIA event subscription |
| Vision+Mouse fallback | 부분 | UIA-evidence one-shot capture gate, local-only profile, authorized PID+HWND, TOCTOU recapture, Windows capture/input, hash-pinned template, independent predicate, 합성 창 actual E2E smoke | 사용자 앱 화면 smoke, 학습 기반 local model, 앱별 profile과 의미적 postcondition |
| Result verification | 부분 | process/file/Notepad/Settings postcondition과 retry/replan 계약 | 모든 신규 adapter의 독립 OS predicate, 실제 UI 변화 recovery set |
| Global/local STOP | 완료(계약) | shared one-way token, Planner·context 우회, 계획/실행 중 blocking worker 병렬 중단, TTS child 종료 | 실제 microphone runtime에서의 latency 측정 |
| Structured audit | 완료(로컬 경계) | redaction, protected ACL, lock, rotation, fsync, SHA-256 chain 및 변조 탐지 | 현재 사용자 계정 침해에 견디는 별도 HMAC/서명 키 |
| Semantic/episodic memory | 완료(기본 범위) | bounded SQLite/FTS5, 30일 retention, credential/HIGH 차단, ACL, 제품 `memory.search` executor와 독립 verifier, 승인 전 DB를 만들지 않는 lazy backend, 실제 DB smoke | 사용자 장기 선호·procedural memory는 P1/P2 |
| 항상대기 통합 runtime | 부분 | 장기 frame pump와 비동기 planning/execution worker를 결합하고 작업 중 exact STOP만 동시 수락 | 실제 마이크 장시간 운용, 장치 복구와 STOP latency |
| Voice feedback/TTS | 부분 | 고정 문구만 허용, SAPI probe, 실제 한국어 WAV·speaker 출력 PASS, 실행 중 STOP | 사용자 평점 3/5의 번역기 같은 말투 개선과 재평가 |

## P0 대표 앱

| 앱 | 현재 증거 | 판정 |
|---|---|---|
| File Explorer | Microsoft path/hash/publisher 등록, allowlisted exact-folder open, 구성요소별 reparse 차단, 새 `CabinetWClass` PID/HWND 및 Shell COM 폴더 독립 검증, 설치 wheel actual 3/3과 exact-window 정리 | 완료(정의된 폴더 열기 범위) — 파일 선택·복사·임의 UI 조작은 별도 capability 필요 |
| Chrome/Edge | Chrome Google 서명·hash 등록, 새 exact PID/HWND/class window actual launch·독립 검증·정리, exact-host HTTPS navigate actual PASS, Edge 실제 launch PID/session identity 및 UIA 구조 관찰 | 부분 — 실제 page content read/change adapter 없음 |
| Notepad | 실제 runtime handoff, Document UIA replace, 독립 text hash | 완료(정의된 replace 범위) |
| Windows Settings | 네 allowlisted URI activation, runtime path/hash, locale-independent fixed UIA page anchor actual 4/4 PASS와 verified-frame cleanup | 완료(정의된 페이지 열기 범위) |
| PowerShell/Terminal | 고정 read-only PowerShell 및 실제 Terminal launch/UIA 관찰 | 완료(정의된 read-only/launch 범위) |

## 연구·평가 요구

| 요구 | 상태 | 현재 증거 | 부족한 증거 |
|---|---|---|---|
| B0/B1/P1/P2 비교 | 부분 | strict paired data loader, metric/통계 분석기 | 실제 snapshot VM trial 또는 participant data |
| 15개 통합 시나리오 | 부분 | strict manifest와 격리, 요구 이름 23/23 cataloged, S01 Chrome process/window actual+cleanup PASS, S02 exact 30% actual+restore PASS, S03 memory/PDF/Edge-window actual fixture+cleanup PASS, S11 actual read-only·S14/S15 policy 3/3 PASS, S06/S13 mock 2/2 PASS·외부효과 0, S04/S05/S07 disposable fixture E2E 3/3 PASS, package-verify/reset-prepare 계약, signed exact-cell snapshot evidence consumer | 외부 snapshot harness의 실제 50셀, 실제 package install, 웹·음성·메시지 및 반복 결과 |
| UER/Unsafe/Underclassification/TSR/Recovery/CB | 부분 | 계산 계약과 Wilson/McNemar/Holm 보고서 | 사전등록된 실제 관찰값 |
| 음성 FAR/FRR/CER/WER/Macro-F1 | 부분 | offline corpus manifest/hash/WAV, 거리·소음·세션별 FAR/FRR, 학습·보정·시험 분리 계획과 CER/WER 하네스 | 동의된 한국어 음성 corpus 및 wake/intent metric |
| 30~50개 작업 80% 목표 | 미검증 | S01~S15별 nominal/fault 30-task strict manifest, schema 1.2 선언/실행 환경 분리, exact 120셀 수집·재개·artifact schema 1.1 assertion 결과 검증. 현재 PC에서 가능한 B0 4, B1 22, P1 22, P2 22를 합쳐 실제 70/120 완료, runner 부재 0, snapshot VM 부재 50. B1/P1은 system verifier·replan 없이 조건 차이를 보존한다 | snapshot VM 50셀, B0/B1/P1/P2 완전 반복 결과와 실제 80% 달성 증거 |
| RED 무승인 실행 0 | 부분 | policy/approval 단위·통합 계약에서 0 | 실제 VM 위험 scenario 반복 증거 |
| recoverable failure 60% | 미검증 | fault injection 및 recovery metric 계약 | 실제 또는 VM UI failure 결과 |

## 현재 릴리스 증거

- Python 전체: 826 passed, 3 skipped, line coverage 83%.
- Ruff clean, mypy strict 126 source files.
- Windows Hello helper build: warning 0, error 0.
- `javis doctor`: SAPI와 optional pycaw를 포함한 15개 check PASS.
- 새 wheel은 130 entry, 307,559바이트, SHA-256 `0ec1f27d267a85059ddf4bf987f81c537280c515170148d70d4cb5e99642db72`이다. 빈 venv에 필수 dependency와 함께 설치해 실제 pilot 조건 안내 공개 API smoke를 재현했다.
- current frozen one-folder는 1,826-entry manifest exact 검증을 통과했고 package manifest SHA-256은 `d1203f8456718ab130523966b545fce1070a98789d523652252d58d4432d8fa0`이다. frozen 비동의 안전 종료, Credential `PRESENT`, preflight 3/3과 13-capability service `CREATED`를 재현했다. 260,362,240-byte per-user Setup SHA-256은 `3f278b54d655577cf0cb53f4bdc8f222c325b36323b9176755c35f2ecc0bc26f`이며 sidecar와 일치한다. 새 Setup은 생성·감사했지만 실제 설치는 아직 실행하지 않았다. 이전 산출물의 최초 설치·manifest·preflight·autostart `ABSENT` 증거는 유지된다. console/tray/Setup은 Authenticode 미서명이므로 공개 배포 완료는 아니다.
- 실제 local voice composition smoke: openWakeWord와 faster-whisper bundle 초기화 및 `VoicePipeline` 조립 PASS, microphone 미개방.
- synthetic closed-loop product composition: wake/VAD/STT, typed intent/plan, policy, executor, verifier, audit, 완료 event PASS; 외부효과 0.
- 실제 product-host preflight: config, protected NIM secret, doctor 3/3 PASS, `ready=true`, exit 0. 마이크·모델 inference·앱·HUD는 열지 않음.
- 실제 product-service check: Windows adapter 13개(`memory.search` 포함), privacy-minimized foreground context, local voice/NIM/approval/Hello/audit/runtime/capture 조립 PASS, capture 미실행, host `CREATED`.
- 설치 wheel 실제 `explorer.folder.open`: exact workspace folder, digest binding, 경로 비노출, 독립 Shell COM 검증과 실제 HWND 소멸 확인을 연속 3/3 PASS.
- 실제 `browser.navigate`: Chrome `https://github.com` 새 창의 PID/HWND/image/creation-time과 URL digest 독립 검증 PASS, exact HWND `WM_CLOSE` 정리 PASS. Edge는 단일 새 창을 증명하지 못해 fail-closed.
- 사용자 참여 검증 CLI: 단계별 fresh consent, 비동의 callback 0회, protected JSONL과 raw audio/transcript
  비저장 계약 PASS. 실제 TTS speaker와 Windows Hello는 PASS했고, mic와 STOP은 호출어 미검출로 FAIL했다.
  비대화형 smoke의 4개 SKIPPED 결과는 이 실제 사용자 참여 결과와 별도로 유지한다.
- 실제 NIM read-only 폐루프: 한국어 명령 → COMMAND gate → `system.info.read` typed plan → policy/executor/verifier → `ALL_ACTIONS_VERIFIED`, audit 5 records, 외부효과 0.
- 종합 safe evaluation: 8 PASS, 7 NOT_RUN, `all_required_evidence_complete=false`.
- 원 계획서 릴리스 전 자동화 체크리스트 13개는 `RELEASE_CHECKLIST_EVIDENCE.md`에서 각각 직접
  테스트에 결합했다. 실제 장치·VM 증거가 필요한 항목은 계약 PASS와 구분했다.
- 배포 대상 실제 API key exact-match scan clean. `NIM.txt`는 패키징 범위에서 제외했고 운영 key는
  current-user Windows Credential Manager 고정 target으로 마이그레이션했다. 기존 파일은 복구용으로 보존했다.

## 다음 완료 순서

1. 4표본 pilot과 분리된 더 큰 보정·최종시험 corpus를 수집하고 맞춤 `Jarvis` 후보를 같은 FAR/FRR 기준으로 비교한다.
2. 기준을 통과한 호출어 모델로 상시 capture의 실제 장시간 운용과 concurrent STOP latency를 측정한다.
3. TTS 말투를 개선하고 사용자 재평가를 수행한다.
4. 실제 사용자 앱별 semantic Vision profile과 독립 postcondition을 추가한다.
5. 내보낸 50개 exact-cell 요청을 외부 snapshot-VM harness에서 실행하고 서명 evidence를 수집한다.
6. 30~50 task 및 B0/B1/P1/P2 실제 데이터 수집 후 통계 보고서 생성한다.

따라서 현재 코드는 안전 제어 prototype으로는 강한 증거가 있지만, 원 계획서가 말한 항상대기
완성품과 졸업논문 실험 결과까지 완료됐다고 판정할 수 없다.
