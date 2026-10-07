# NIM 생성 및 감사 기록

## 2026-09-29 audio.volume.read 초안 요청

- `z-ai/glm-5.3`에 read-only `audio.volume.read`의 코드·테스트·한국어 문서 초안을 700-token으로
  요청했으나 35초 HTTP read timeout으로 끝났다.
- NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다. Codex가 고정 endpoint,
  무인자 계약, bounded lock, cancellation, stable redacted error와 독립 readback verifier를 구현하고
  대상 테스트·Ruff·strict mypy로 감사했다.

## 2026-09-29 S03 memory-to-PDF 검토 요청

- `z-ai/glm-5.3`에 검증된 단일 memory ID를 허용 root의 hash-pinned PDF action으로 변환하는 설계의
  보안 테스트와 한국어 문서 검토를 350-token으로 요청했으나 20초 HTTP read timeout으로 끝났다.
- NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다. Codex가 위조 검색 영수증,
  다중 결과, root 이탈, non-PDF와 symlink를 거부하는 테스트를 추가해 감사했다.

## 역할 분리

- NVIDIA NIM: README 및 Python 타입·레지스트리·정책 코드의 초안 생성
- Codex: 요구사항 분해, 프롬프트 작성, 생성물 검토, 오류 수정, 테스트 설계 및 실행

초기 사용 모델은 `deepseek-ai/deepseek-v4-flash-0731`이었다. 2026-09-22 해당 ID가 실제
endpoint에서 HTTP 410을 반환해 NVIDIA 공식 catalog에 표시된 `z-ai/glm-5.3`으로 transport
기본값을 갱신했다. API 키 값은 프롬프트, 코드, 문서 또는 테스트 출력에 포함하지 않았다.

## 발견한 초안 문제

NIM 결과는 그대로 반영하지 않았다. 감사 과정에서 다음 문제를 수정했다.

- `CapabilitySpec`의 실제 필드명과 다른 `id`, `risk_level`, `executors` 사용
- `Action.capability_id` 대신 존재하지 않는 `Action.capability` 접근
- 주입받아야 할 registry를 함수 안에서 잘못 재생성
- 정의되지 않은 `Decision.ALLOW`와 `risk_level` 필드 사용
- YELLOW와 RED를 정책 의미에 맞게 반환하지 못함
- 존재하지 않는 `strong_auth_required` context 접근
- `requested_risk=None` 처리 누락
- registry의 `get()`과 `require()` 의미 혼동
- 코드 응답 일부가 토큰 제한으로 잘림
- README에서 UER를 Unsafe Execution Rate로 잘못 확장한 부분 수정
- 클라우드 비사용을 비목표로 단정한 부분을 구현 선택사항으로 수정

## 적용한 안전 보정

- 모든 Pydantic 모델에 `extra="forbid"`와 불변 설정 적용
- unknown capability를 `DENY/R4`로 고정
- 위험도 합성을 `max()` 기반 상향 전용으로 구현
- prompt injection 의심, 낮은 발화 게이트, 대상 불일치를 hard deny로 처리
- RED approval을 정확한 plan/action/arguments/target/risk에 결합
- HMAC 비교에 constant-time 비교 사용
- 120초 기본 만료 및 nonce 1회 소비로 replay 차단
- secret과 token을 로그에 남기는 코드 배제
- 실제 OS 부작용을 포함하지 않는 테스트만 작성

## 검증 결과

2026-09-20 기준 Python 3.12 환경에서 다음 결과를 확인했다.

- pytest: 17 passed
- line coverage: 93%
- Ruff: all checks passed
- mypy strict: no issues in 5 source files

이 결과는 Phase 0 정책 코어에 한정된다. 실제 Windows executor, 음성 파이프라인, UIA/Vision, 사용자 연구 성능을 증명하지 않는다.

## Phase 1·2 생성 기록

- runtime model과 JSONL audit sink 초안을 NIM에 요청했다.
- runtime model 초안은 잘못된 import를 교정한 뒤 계약 설계에 반영했다.
- audit sink 초안은 민감 키 분기 양쪽이 같은 재귀 호출이어서 실제 마스킹이 되지 않았고 응답도 잘렸다. 해당 로직은 폐기하고 테스트 우선으로 다시 구현했다.
- Native read-only executor 초안 요청은 API timeout으로 결과를 받지 못했다. 이 항목은 고정된 요구사항을 기준으로 Codex가 구현했다.
- NIM 응답 성공 여부와 무관하게 최종 코드는 pytest, Ruff, mypy를 모두 통과해야 채택한다.

## 실제 Planner 통합 감사

- 프로젝트의 `NIM.txt` 키를 프로세스 환경에서 즉시 제거하는 방식으로 실제 NIM 계획 생성을 시험했다.
- 첫 계획은 허용되지 않은 postcondition을 생성했다. 실행기는 동작했지만 verifier가 `UNKNOWN_POSTCONDITION`으로 task를 중단했다.
- 이 결과를 바탕으로 Planner 경계에 postcondition allowlist와 action별 최소 1개 postcondition 규칙을 추가했다.
- 같은 목표를 재시험한 결과 NIM은 `system.info.read`와 `execution.success`로 구성된 단일 단계 plan을 반환했다.
- 해당 plan은 typed contract, 정책, Native read-only executor, verifier를 순서대로 통과했다.
- 이 smoke test는 무해한 read-only capability에 한정되며 위험 action의 자동 실행을 허가하지 않는다.

## 영구 move journal 생성 감사

- NIM에 append-only JSONL journal과 시작 복구 판정기 초안을 요청했다.
- 응답은 파일 중간에서 잘렸으며 완결된 코드나 테스트를 제공하지 못했다.
- Codex가 부분 초안의 방향만 참고하고, 부분 write 탐지, `fsync`, 손상 위치 판별,
  terminal 상태 멱등성, 무변경 recovery 규칙을 포함하도록 다시 구현했다.
- recovery는 원본·목적지 파일에 쓰기 작업을 하지 않으며, 양쪽 존재·양쪽 부재·해시 불일치는
  모두 `AMBIGUOUS`로 종결한다.
- journal 경로는 개인정보가 될 수 있으므로 비밀 로그와 별개로 민감 데이터로 분류했다.
- 2026-09-21 전체 검증 결과: pytest 119 passed, line coverage 90%, Ruff 및 mypy strict 통과.

## 로컬 승인 UI 생성 감사

- NIM에 로컬 승인 presenter와 OS-backed strong authenticator 초안을 두 차례 요청했다.
- 두 요청 모두 응답 본문 없이 timeout되어 생성 코드는 채택할 수 없었다.
- Codex가 기존 typed approval 계약과 Microsoft의 desktop interop 요구사항을 대조해 구현했다.
- UI는 capability, 목적, 대상, 위험도, 정확한 JSON 인자, 요청 ID, 추가 인증 필요 여부를
  표시하며 닫기·timeout·예외는 모두 거부한다.
- R4 adapter는 OS-owned prompt의 `VERIFIED` 결과만 수락하며 PIN·비밀번호 또는 임의의
  `authentication_proof` 문자열을 신뢰하지 않는다.
- 실제 Windows Hello provider는 HWND 기반 `UserConsentVerifierInterop`가 필요하므로 아직
  완료로 표시하지 않았다. provider 부재는 fail-closed다.
- 2026-09-21 단계 검증 결과: pytest 127 passed, Ruff 및 mypy strict 통과.

## App registry 생성 감사

- 최신 NIM catalog를 확인해 `nvidia/nemotron-3.5-lightning-30b-a3b`로 초안을 요청했다.
- endpoint는 요청을 수신했지만 60초 read timeout으로 응답 본문을 반환하지 않았다.
- Codex가 strict Pydantic JSON schema, 파일 크기·앱 개수 제한, 중복 ID 거부, symlink·상대
  경로 거부, 로드 시 SHA-256 확인을 구현했다.
- publisher 문자열을 단순 비교만 하지 않고, 고정된 PowerShell script가
  `Get-AuthenticodeSignature`의 `Valid` 상태와 signer certificate subject를 함께 확인한다.
- 경로와 publisher는 환경 변수로만 전달되며 PowerShell 코드에 보간하지 않는다. timeout,
  cmdlet 오류, 서명 부재, subject 불일치는 모두 거부한다.
- 실행 직전 `ProcessLaunchExecutor`가 같은 경로와 hash를 다시 확인하므로 registry 로드 뒤
  파일 교체도 차단한다.

## 음성 adapter 구현 감사

- 이 단계에서는 앞선 연속 NIM timeout 때문에 새 생성 응답을 기다리지 않고 Codex가 공식
  openWakeWord 및 faster-whisper API 문서를 기준으로 adapter를 작성했다.
- openWakeWord 입력은 mono 16 kHz signed 16-bit PCM으로 제한하고 지정 wake-word key만 읽는다.
  비수치·NaN score는 0으로 처리하며 범위를 0~1로 제한한다.
- faster-whisper는 local model directory와 `local_files_only=True`만 허용하고 한국어
  `transcribe`, 이전 문맥 비사용, transcript 길이 제한을 적용했다.
- optional package가 없으면 import 시 전체 core가 실패하지 않고 adapter 생성 시 명시적으로
  실패한다. 실제 모델 품질 주장은 하지 않는다.

## Fault injection runner 생성 감사

- NIM transport에 요청별 `max_tokens` 상한을 추가하고 600토큰의 짧은 초안을 요청했다.
- NIM은 응답 envelope를 반환했지만 `content`가 비어 있어 초안으로 사용할 수 없었다.
- Codex 구현은 fault를 `BEFORE_CALL`과 `AFTER_CALL`로 분리한다. 호출 전 fault만 명시적으로
  retry-safe가 될 수 있고, 호출 후 fault는 delegate 결과와 무관하게 부작용 가능 상태로
  기록하고 자동 재시도를 금지한다.
- report에는 action arguments를 저장하지 않고 fault 이름, phase, call index, action ID,
  ambiguity와 retry 판단만 JSONL로 내보낸다.
- 2026-09-21 단계 검증 결과: pytest 149 passed, Ruff 및 mypy strict 통과.

## Windows Hello IPC 경계 감사

- `nvidia/nemotron-3-nano-30b-a3b`에 helper client 초안을 요청했으나 endpoint가 HTTP 410을
  반환해 초안을 받지 못했다.
- Microsoft 문서를 확인한 결과 desktop 호출은 활성 application HWND를 넘기는
  `IUserConsentVerifierInterop::RequestVerificationForWindowAsync`를 사용해야 하며 최소
  지원 client는 Windows build 22000이다.
- 첫 단계에는 SDK가 없어 Python client만 구현했지만, 후속 단계에서 Microsoft 공식
  `dotnet-install.ps1`로 SDK 9.0.306을 프로젝트 `.dotnet`에 격리 설치했다. 시스템 PATH와
  레지스트리는 변경하지 않았다.
- `nvidia/nemotron-3-super-120b-a12b`가 C# helper 초안을 생성했다. 초안의 잘못된 result enum,
  누락된 response `schema_version`, Python과 맞지 않는 소문자 result, desktop active HWND를
  충족하지 못하는 desktop-window owner, message pump 부재를 채택하지 않았다.
- Codex 교정 구현은 표시되는 WinForms 창의 HWND로
  `UserConsentVerifierInterop.RequestVerificationForWindowAsync`를 호출하고, 사전에
  `UserConsentVerifier.CheckAvailabilityAsync`를 확인한다. `net9.0-windows10.0.22621.0`
  빌드가 warning 0으로 성공했다.
- 두 번째 NIM 문서 초안은 `schema_version`을 숫자로 적고, JSON stdout을 CSV라고 설명했으며,
  가용성 API의 소유 class와 구현되지 않은 HWND 검증을 잘못 주장했다. 해당 내용은 소스·빌드
  결과와 대조해 모두 수정했다.
- Python client는 trusted-root-relative manifest를 절대 경로로 해석하고 symlink·traversal을
  거부한다. helper SHA-256을 매 호출 확인하며 one-time challenge, 1,024자/8 KiB request,
  4 KiB response, timeout, strict JSON schema를 적용한다.
- 실제 EXE에서 empty/malformed/extra field/control character/빈 UUID/과대 입력이 nonzero와
  무출력으로 거부됨을 확인했다. 이후 재빌드한 unsigned single-file apphost는 이 PC의 Windows
  application-control 정책에 `WinError 4551`로 차단됐다. 정책을 우회하지 않고 Authenticode가
  `Valid`인 Microsoft system `dotnet.exe`를 host로 사용하도록 변경했다. host와 managed DLL,
  deps/runtimeconfig, Windows SDK projection DLL 두 개를 모두 개별 SHA-256으로 고정하고 publish
  디렉터리의 정확한 파일 집합도 매 호출 검사한다. 이 hosted 경로에서 malformed input 검증이
  통과했다. 대화형 smoke는 제한 시간 안에 사용자 인증 성공을 얻지 못해 `FAILED`로 닫혔으며
  `VERIFIED` 성공 경로와 별도 서명 apphost는 아직 주장하지 않는다.
- 2026-09-21 단계 검증 결과: pytest 154 passed, line coverage 85%, Ruff 및 mypy strict 통과.

## UIA observation 감사

- NIM endpoint가 timeout·빈 content·410 상태를 연속 반환해 이 단계에는 채택 가능한 생성물이
  없었다. Codex가 pywinauto 공식 `find_elements`/UIA element property 문서를 기준으로 작성했다.
- selector는 PID를 필수로 하고 AutomationId, ControlType, Name 중 하나 이상의 exact property를
  요구한다. 정규식, best-match, 좌표 추정은 허용하지 않는다.
- backend 결과는 다시 PID와 모든 selector field를 비교하며 0개와 복수 결과를 모두 실패로
  처리한다.
- `ui.element.inspect`는 검증된 launch session의 PID만 허용한다. session registry는 접근할
  때마다 process image, 등록 SHA-256, process creation time을 재확인하고 실패하면 권한을
  폐기한다. 따라서 동일 PID가 새 프로세스에 재사용되어도 권한이 승계되지 않는다.
- element의 name이나 value는 execution observation에 포함하지 않고 structural identity와
  enabled/visible 상태만 반환한다.
- 2026-09-21 단계 검증 결과: pytest 170 passed, Ruff 및 mypy strict 통과.

## Notepad adapter 및 운영 registry 감사

- `mistralai/mistral-nemotron`에 제한된 Notepad adapter 초안을 요청했지만 60초 read timeout으로
  응답을 받지 못했다.
- Codex 구현은 selector를 planner arguments에서 제거하고 trusted `NotepadProfile`에 고정했다.
  action은 PID와 text만 포함하며 target은 `process:{pid}:notepad-editor`와 일치해야 한다.
- PID는 단순 authorized 여부뿐 아니라 session의 app ID가 정확히 `notepad`인지 확인한다.
- pywinauto의 일반 `set_edit_text`는 action log에 원문을 남길 수 있어 사용하지 않고,
  UIA ValuePattern `SetValue`를 직접 호출한다. execution result에는 text 대신 SHA-256만 남긴다.
- mutation 전 runtime ID를 재확인하고, mutation 뒤 UIA에서 읽은 text hash와 expected hash를
  비교한다. 별도 verifier predicate도 같은 값을 다시 읽는다.
- 현재 PC의 `C:\Windows\System32\notepad.exe`는 Authenticode `Valid`, Microsoft Windows
  publisher임을 확인해 `config/apps.json`에 경로·SHA-256·publisher를 고정했다.
- `AppRegistryLoader(require_publisher=True)`와 CLI로 실제 registry 검증을 통과했다.
- 2026-09-21 단계 검증 결과: pytest 176 passed, Ruff 및 mypy strict 통과.

## 휴지통 executor 감사

- `poolside/laguna-xs-2.1`에 제한된 휴지통 executor 초안을 요청했으나 NIM endpoint가 HTTP
  503을 반환했다.
- Codex 구현은 절대 경로, symlink 금지, allowed root, exact target identity, pre-call hash
  재검사를 적용한다. directory와 영구 삭제는 지원하지 않는다.
- backend 호출 뒤 예외·source 잔존은 side effect 가능 상태로 보고 항상
  `retry_safe=False`다. 성공 뒤 별도 `file.absent` predicate가 경로를 다시 확인한다.
- `send2trash` optional dependency를 설치하고 프로젝트 내부 일회성 fixture 하나를 실제
  Windows 휴지통으로 이동했다. 원본 부재와 structured result를 확인했으며 fixture는 휴지통에서
  복구 가능하다.
- 2026-09-21 단계 검증 결과: pytest 180 passed, Ruff 및 mypy strict 통과.

## Audit retention 보강

- in-memory audit sink를 기본 10,000 event의 bounded deque로 변경했다.
- JSONL sink에 symlink 거부, configurable maximum bytes, bounded backup rotation,
  append `fsync`, best-effort mode 0600을 추가했다.
- rotation과 move journal append/load에 별도 1-byte Windows interprocess lock을 추가했다.
  process-local thread lock도 유지하며 lock timeout은 fail-closed다.
- 실제 별도 file handle이 lock 보유 중 2차 acquire를 거부하는 contention test를 추가했다.
- 2026-09-21 단계 검증 결과: pytest 185 passed, Ruff 및 mypy strict 통과.

## Windows ACL 경계 감사

- `deepseek-ai/deepseek-v4-flash-0731`에 Windows ACL 초안을 요청했으나 NIM endpoint가 HTTP
  410을 반환해 생성물을 받지 못했다.
- Codex가 설치된 pywin32의 실제 상수와 API signature를 확인한 뒤 ACL 경계를 구현했다.
- audit, move journal 및 각 lock 파일은 symlink와 비정규 파일을 거부하고 현재 process token의
  사용자 SID와 LocalSystem SID에만 full control을 부여하는 protected DACL을 사용한다.
- ACL 적용 직후 security descriptor를 다시 읽어 DACL protection, ACE 개수, SID 집합,
  ACCESS_ALLOWED type, inheritance flag 부재, access mask가 모두 정확한지 검증한다.
- 파일 소유자와 SACL은 변경하지 않는다. ACL 생성·적용·검증 중 하나라도 실패하면 기록 작업을
  계속하지 않는 fail-closed 동작으로 테스트했다.
- 실제 Windows 임시 파일과 audit data/lock 파일을 대상으로 ACL round-trip test를 수행했다.
- 2026-09-21 전체 검증 결과: pytest 187 passed, line coverage 85%, Ruff 및 mypy strict 통과.

## Snapshot VM evidence gate 감사

- NIM catalog에서 모델 ID를 조회했다. `meta/llama-3.1-8b-instruct`는 HTTP 410,
  `mistralai/codestral-22b-instruct-v0.1`은 HTTP 404를 반환했다.
- `nvidia/nemotron-3-super-120b-a12b`는 초안을 반환했다. 첫 응답은 Windows CP949 출력에서
  유니코드 문자를 표현하지 못해 표시가 실패했고, UTF-8 출력으로 같은 요청을 다시 수행했다.
- NIM이 제안한 순수 evidence gate, bounded JSON, injected signature verifier, manifest binding,
  freshness, deterministic report 방향은 채택했다.
- 초안의 Pydantic v1 validator, timezone 없는 시각을 UTC로 묵인하는 동작, datetime을 직접
  `json.dumps`하는 canonicalization 오류, 불명확한 서명 대상은 채택하지 않았다.
- 최종 구현은 Pydantic v2 strict model, 중복 key·추가 field·symlink·비정규 파일·비유한 수
  거부, 명시적 UTC, 서명 field만 제외한 canonical payload, verifier 예외 fail-closed를 적용했다.
- core에는 hypervisor 제어 또는 위험 action 실행 경로가 없다. 실제 VM 실행과 `(scenario_id,
  run_id)` replay 저장소는 외부 harness 책임으로 명시했다.

## 한국어 STT corpus 평가 하네스 감사

- `nvidia/nemotron-3-super-120b-a12b`가 offline corpus loader, WAV 검증, NFKC normalization,
  micro CER/WER, privacy-minimized report의 코드·문서 초안을 생성했다.
- 초안은 기존 `SpeechToText.transcribe(frames)` 대신 새로운 path 기반 protocol을 정의해 호환되지
  않았고, validation failure를 sample error로 보고하는 부분이 fail-closed 요구와 충돌했다.
- 최종 구현은 전체 corpus의 경로·hash·WAV format·duration 검증을 추론 전에 완료한다. 검증
  실패 시 STT 호출 수가 0임을 테스트했고, 실제 model inference 예외만 sample 단위로 격리한다.
- CER은 정규화 후 공백을 제외한 code point, WER은 공백 token 기준 micro average로 고정했다.
  inference 실패도 빈 hypothesis로 포함해 metric에서 제외하지 않는다.
- 보고서에서 audio, 경로, transcript 원문, consent record ID를 제거했다. hash도 민감 데이터일 수
  있음을 별도 문서에 명시했다.

## B0/B1/P1/P2 paired-study 분석 감사

- `nvidia/nemotron-3-super-120b-a12b`가 Wilson interval, exact two-sided McNemar, P2 대 세
  비교군의 지표별 Holm 보정 초안을 생성했다.
- 초안은 block 일관성을 participant/scenario ID 동일성으로만 설명했으나, 최종 구현은 네 조건의
  `intended_command`와 `ground_truth_risk`가 정확히 같아야 한다고 강제한다.
- UER, Unsafe Execution Rate, TSR마다 eligible block을 먼저 고정해 네 조건의 분모와 paired
  comparison 표본이 같도록 했다. eligible block이 없으면 0 대신 null을 보고한다.
- participant ID는 grouping에만 쓰고 report에는 넣지 않는다. p-value로 유의성을 자동 선언하지
  않으며 effect 방향과 추가 연구 해석 책임을 별도 문서에 명시했다.

## Windows 실행 파일 lease 감사

- `nvidia/nemotron-3-super-120b-a12b`가 `CreateFileW`, `GetFinalPathNameByHandleW`, `ReadFile` 기반
  lease와 replacement denial test 초안을 생성했다.
- 초안은 final-path flag 설명과 launcher callback 권한이 불명확했다. 최종 구현은 flag 0의
  normalized DOS path를 exact canonical path와 비교하고 callback에 raw handle을 노출하지 않는다.
- `GENERIC_READ`와 `FILE_SHARE_READ`만 사용해 write/delete sharing을 허용하지 않고, handle에서
  SHA-256을 계산한 뒤 기존 no-argument launcher가 반환될 때까지 handle을 유지한다.
- 실제 Windows temp 파일에서 lease 중 overwrite와 `os.replace`가 거부되고, launcher 예외 뒤
  handle이 해제되어 다시 쓸 수 있음을 검증했다.
- lease 획득·검증 실패와 launch 후 실패는 모두 retry-safe가 아니며 관리자·kernel 공격에 대한
  완전한 보호는 주장하지 않는다.

## Read-only microphone capability probe 감사

- `nvidia/nemotron-3-super-120b-a12b`가 장치명·host API·audio·예외 원문을 반환하지 않는
  sounddevice capability probe 초안을 생성했다.
- 초안은 기본 장치 하나만 반환하는 방향과 `check_input_settings`가 bool을 반환한다는 가정을
  섞었다. 실제 API는 성공 시 반환 없이 완료하고 미지원 시 예외를 내므로 그 의미로 보정했다.
- 최종 구현은 최대 256개 endpoint를 열거해 입력 channel이 있는 장치마다 mono, 16 kHz,
  int16 설정을 검사한다. stream 생성·callback·audio read 경로는 없다.
- 2026-09-21 현재 PC에서 실제 read-only probe가 28개 endpoint, 입력 16개, 호환 9개와 호환되는
  기본 입력을 보고했다. 장치명과 driver 예외 내용은 출력되지 않았다.

## Local wake-word bundle 감사

- local ONNX feature bundle loader 보정과 strict manifest loader 초안 요청은 각각 NIM endpoint의
  HTTP 503으로 실패해 생성물을 받지 못했다.
- Codex가 설치된 openWakeWord 0.6.0 source를 확인해 `Model`이 ONNX mode에서 wake-word 외에
  `melspectrogram.onnx`, `embedding_model.onnx`를 필요로 함을 확인했다.
- 기존 adapter가 `.onnx` 하나만 확인하고 upstream default에 의존하던 문제를 수정해 세 local
  regular file과 `inference_framework="onnx"`를 명시적으로 전달한다.
- openWakeWord import 과정에서 SciPy 1.18.1의 unsigned `_tools.pyd`가 Windows application-control
  policy에 차단됐다. 정책을 우회하거나 파일을 unblock하지 않고 호환 범위의 SciPy 1.16.3으로
  교체해 SciPy와 openWakeWord import를 통과시켰다.
- 공식 v0.5.1 GitHub release의 ONNX 세 파일을 내려받아 SHA-256 manifest에 고정했다. strict
  loader 검증 후 실제 offline 80 ms 무음 inference는 score 0.0으로 성공했다.

## Local faster-whisper bundle 감사

- CTranslate2 bundle manifest loader 초안 요청은 NIM endpoint HTTP 503으로 실패했다.
- Hugging Face API에서 공식 `Systran/faster-whisper-tiny` repository의 immutable commit과 파일
  목록·크기를 먼저 조회했다. moving branch 대신 commit SHA에 고정된 URL에서 필수 네 파일만
  받았다.
- strict loader는 source revision, 정확한 filename, 선언 size, 실제 size, SHA-256, symlink,
  per-file/total bound를 확인하고 초기화 직전 `verify()`를 다시 수행한다.
- 실제 local-only CPU int8 model load와 1초 무음 inference가 성공했으며 빈 transcript를
  반환했다. 모델의 한국어 품질 주장은 하지 않는다.

## Notepad runtime handoff 및 bounded UIA 감사

- launcher/runtime identity 분리 초안은 NIM에서 받았지만 runtime publisher를 무조건 필수로
  만드는 부분이 backward compatibility와 충돌해 채택하지 않았다. path+hash만 한 쌍으로
  강제하고 publisher는 운영 `require_publisher=True`에서 각 identity별로 필수화했다.
- 실제 Windows 11 Notepad는 launcher PID가 종료된 뒤 하나 이상의 signed WindowsApps process를
  잠시 생성했다. 첫 후보 즉시 승인은 race를 일으켜 폐기하고, 2초 안정화 후 최종 exact 후보가
  하나인 경우만 승인하도록 수정했다.
- UIA bounded subprocess 초안 요청은 NIM HTTP 503으로 실패했다. Codex가 고정 module, `-I`,
  shell 비사용, 최소 환경, strict request/response, 요청·응답 크기, 5초 timeout 경계를 구현했다.
- 실제 selector는 AutomationId가 비어 있고 `ControlType=Document`, class `RichEditD2DPT`였다.
  trusted profile을 이 modern Notepad contract로 확장했다.
- 새 미저장 Notepad에서 launcher→runtime session 승인, bounded UIA ValuePattern replace, 즉시
  SHA-256 검증, 별도 predicate 재검증이 모두 성공했다. 원문은 report에 남기지 않았고 새 PID만
  저장 없이 종료했다.

## Edge·Terminal 등록 및 ClassName UIA 감사

- Edge, Explorer, Windows Terminal의 실제 설치 경로·SHA-256·Authenticode `Valid`·Microsoft
  certificate subject를 읽기 전용으로 확인하고 `config/apps.json`에 고정했다. 운영
  `require_publisher=True` loader가 Notepad를 포함한 네 앱을 모두 검증했다.
- 새 Terminal PID는 launch 뒤 session identity 검증에 성공했고 생성한 process tree만 종료했다.
  기존 Explorer shell은 종료 위험 때문에 실행 smoke를 하지 않았다.
- 새 Edge는 launcher PID가 main browser PID였지만 `Window`가 둘이라 ControlType 단독 selector가
  ambiguity로 거부됐다. 페이지 title·URL·content는 수집하지 않고 structural metadata만 확인해
  main window class가 `Chrome_WidgetWin_1`임을 찾았다. Terminal main class는
  `CASCADIA_HOSTING_WINDOW_CLASS`였다.
- `nvidia/nemotron-3-super-120b-a12b`가 optional exact `class_name` selector 확장 초안을 만들었다.
  초안이 `process_id` 자체를 element identity로 세도록 제안한 부분은 PID-only tree 조회를
  허용하므로 채택하지 않았다. 존재하지 않는 mutable selector 경로 설명도 제거했다.
- 최종 구현은 PID를 별도 필수 경계로 유지하고 AutomationId/ControlType/Name/ClassName 중 하나
  이상의 exact identity를 요구한다. class는 max 256자로 제한하고 pywinauto lookup과 독립 snapshot
  재검사 양쪽에 적용한다. regex·부분 일치는 없다.
- 새 Edge와 Terminal에서 launch→hash/session authorization→bounded child UIA→exact main window
  observation이 성공했다. 반환값은 PID/runtime ID/class/control/enabled/visible뿐이며 UI text는
  포함하지 않았다. 생성한 process tree를 종료한 뒤 Edge/Terminal process가 남지 않음을 확인했다.

## SQLite semantic/episodic memory 감사

- 원 계획서의 SQLite+FTS5, 사용자 삭제 가능한 semantic memory, 30일 episodic summary, credential
  저장 금지 요구를 기준으로 `nvidia/nemotron-3-super-120b-a12b`에 코드·문서 초안을 요청했다.
- NIM 응답의 원칙 목록은 참고했지만 코드는 Pydantic v1 `validator`, 존재하지 않는
  `timezone.ISO`, 잘못된 FTS operator 정규식과 미완성 상수를 포함해 채택하지 않았다.
- 최종 구현은 strict JSON scalar, LOW/MEDIUM만 있는 sensitivity enum, explicit/verified source,
  UTC-only expiry, credential-like pattern 거부, bounded row/string/summary/app/k를 강제한다.
- raw query를 FTS에 넣지 않고 허용 token을 추출해 quoted literal 식만 생성한다. SQL 값은 모두
  parameter binding하며 만료 레코드는 조회에서 제외한다.
- WAL sidecar를 만들지 않고 DELETE journal, `max_page_count`, schema version, absolute non-symlink
  DB와 Windows protected DACL을 적용한다. 첫 실제 smoke에서 connection context가 close를 하지
  않아 `WinError 32` cleanup 실패를 발견했고 `contextlib.closing`으로 모든 transaction handle을
  즉시 닫도록 수정했다.
- 수정 후 실제 DB에서 ACL=True, FTS hit 1, episode retention 30일, semantic 삭제와 임시 디렉터리
  cleanup을 모두 확인했다. 원문 transcript와 action arguments를 저장하는 API는 없다.

## 배포 doctor 및 wheel 감사

- `nvidia/nemotron-3-super-120b-a12b`에 경로·장치·예외·환경값을 노출하지 않는 read-only doctor
  초안을 요청했다. NIM은 검사 항목 원칙은 정리했지만 실제 코드를 완성하지 않았고, 존재하지
  않는 manifest filename, 부적절한 `winreg` publisher 검사, 존재하지 않는 `dotnet-sdk.txt`를
  제안해 채택하지 않았다.
- 최종 구현은 기존 `AppRegistryLoader(require_publisher=True)`, Hello manifest loader,
  wake/STT strict bundle loader를 직접 재사용한다. Hello prompt, 모델 inference, 앱 launch,
  microphone probe는 실행하지 않는다.
- dependency import의 stdout/stderr도 메모리로 억제하고 모든 예외는 stable failure code로만
  바꾼다. required FAIL만 exit 1이며 optional dependency와 rebuild용 SDK 부재는 WARN이다.
- 실제 project root에서 Windows/Python/FTS5/app registry/Hello/model/dependency/SDK 13개가 모두
  PASS했다. 출력에는 로컬 경로나 예외 원문이 없었다.
- `pip wheel --no-deps` 실제 빌드가 성공했고 최신 wheel 59개 entry에 Settings, voice command,
  intent, foreground context, doctor, memory, Windows Hello 모듈과 console entry-point metadata가 포함됨을
  zip 수준에서 확인했다. 모델·PC별 config·native
  managed artifact는 의도적으로 wheel 외부의 명시적 project root에서 hash 검증한다.

## 최소 전경 컨텍스트 감사

- `nvidia/nemotron-3-super-120b-a12b`에 창 제목과 UI 내용을 제외한 foreground context 초안을
  요청했다. 응답은 실행 파일의 basename만 등록값과 비교해 앱을 식별했으므로 동일 이름의 다른
  실행 파일을 신뢰할 수 있어 채택하지 않았다. 기존 registry 모델과 맞지 않는 별도 앱 타입도
  제거했다.
- 최종 구현은 고정 Win32 API로 HWND/PID만 얻고 기존 `WindowsProcessInspector`와
  `RegisteredApp.matches_runtime_executable`을 재사용해 exact resolved path와 현재 SHA-256을
  모두 검증한다. 미등록 앱은 구조 정보만 반환하고 복수 일치는 실패한다.
- snapshot에는 title, UI text, screenshot, command line, executable path가 없으며 authorization
  또는 검증된 process session을 대신하지 않는다는 제한을 별도 문서와 CLI에 고정했다.
- 실제 운영 registry의 Microsoft publisher를 재검증한 CLI smoke에서 현재 전경 앱은 미등록으로
  판정됐고 `app_id=null`, `identity_verified=false`로 닫혔다. 출력에는 제목·경로·내용이 없었다.
- NIM의 후속 안정화 초안은 연속 identity 비교와 verified 기본값은 참고했지만 `min/max_samples`의
  종료 의미가 없고 주입한 monotonic clock을 사용하지 않았으며 내부 예외 원문을 노출했다.
  최종 구현은 정확한 2~10회 표본, 유한 interval/timeout, 비단조 clock·대기/수집 예외 redaction,
  focus 변경 즉시 실패를 강제한다. Planner에는 stable verified app ID 한 필드만 전달한다.

## Typed intent classifier 감사

- strict intent classifier 코드·테스트·한국어 문서 초안을 같은 NIM 모델에 요청했다. 첫 요청은
  60초 read timeout이었고, 축약 재시도는 코드 대신 요청 내용을 되풀이하면서 프로젝트에 없는
  필드 구조와 임의 STOP 문구를 추측했다. 두 응답 모두 구현 초안으로 채택하지 않았다.
- 최종 구현은 기존 `LocalStopDetector`, `IntentEvidence`, `IntentClass`를 재사용한다. exact local
  STOP과 `UNTRUSTED_CONTENT`는 NIM 호출을 우회하며, 후자는 원문도 redacted placeholder로 바꾼다.
- NIM은 CANCEL, wake/source/explicit-wake를 결정할 수 없다. 단일 JSON proposal의 duplicate key,
  markdown, NaN, extra/missing field, 정수 confidence, 숫자 boolean, 크기 초과를 모두 거부하고
  transport/validation 오류에서는 transcript와 내부 예외를 제거한다.
- 무해한 실제 입력 `자비스 메모장 열어`에 대해 NIM이 strict proposal을 반환했고, 로컬
  `IntentGate`가 세 confidence의 최솟값을 적용해 `PLAN`으로 판정했다. 이는 단일 smoke이며 실제
  한국어 corpus 정확도나 calibration을 입증하지 않는다.

## Planner 입력·목표 결합 재감사

- 통합 전 감사에서 기존 Planner가 모델이 반환한 `plan.goal`을 사용자 원문 대신 그대로
  채택하고, JSON duplicate key 및 `default=str`에 의한 비-JSON trusted context 변환을 허용하는
  문제를 찾았다.
- 최종 경계는 goal·untrusted context·전체 user prompt 크기를 제한하고 NUL, duplicate key,
  NaN/Infinity, 비-JSON context를 모델 호출 전에 또는 parse 단계에서 거부한다. 채택된 Plan의
  goal은 모델 문자열이 아니라 공백 정리된 원래 사용자 goal로 강제 결합한다.
- 강화 후 실제 NIM plan 재스모크는 HTTP 503으로 fail-closed 되었으며 executor는 호출되지
  않았다. 따라서 이 시점에는 단위 계약만 재검증됐고 기존 실제 NIM end-to-end 성공 기록을
  새로운 네트워크 성공으로 대체하지 않는다.

## 음성 명령 coordinator 감사

- intent와 Planner를 연결한 직후 NIM endpoint가 HTTP 503 상태였으므로 이 coordinator에는 새로
  채택할 수 있는 NIM 코드 초안이 없었다. 이미 NIM이 생성한 intent proposal과 typed Planner
  경계를 입력으로 사용하되, Codex가 실행 권한 분리와 STOP 연결을 직접 감사했다.
- 최종 coordinator는 capture abort, 빈 transcript, 분류/계획 예외를 stable code로 닫고,
  명확한 Gate `PLAN`만 Planner에 전달한다. 반환 Plan은 아직 권한이 없으며 Orchestrator가 각
  action을 다시 검사한다.
- 활성 작업의 local STOP은 Planner를 우회해 Orchestrator와 공유하는 one-way cancellation token을
  set한다. 실제 구성요소를 사용한 Voice capture→Intent→Gate→Plan→Policy→mock Executor→Verifier
  통합 테스트를 추가했다.

## Windows Settings URI adapter 감사

- 계획서의 P0 앱 범위를 채우기 위해 system `SystemSettings.exe`의 SHA-256과 Microsoft Windows
  Authenticode subject를 확인하고 임시 registry 등록 후 실제 launch를 시험했다.
- launcher PID는 1.5초 뒤 동일 runtime path/hash로 재검증되지 않았고, 별도 `SystemSettings.exe`
  runtime도 남지 않았다. 기존 `ApplicationFrameHost.exe`는 시험 전부터 있던 process여서 대상에서
  제외했다. 이 상태에서 지원을 주장하지 않고 registry 변경을 되돌렸다.
- 후속 NIM 초안은 fixed URI allowlist, 기존 단일 runtime 재사용, 모호성 거부 원칙은 제안했지만
  프로젝트에 없는 `identity/path` 앱 타입을 다시 만들었고 코드가 중간에 잘려 그대로 채택하지
  않았다.
- 최종 구현은 registry에 strict `direct_launch=false`를 추가해 generic launch를 차단하고,
  `settings.open` typed contract에서 5개 page ID만 허용한다. 이 choice 목록은 Planner prompt에도
  포함된다. 전용 adapter는 URI를 내부 상수에서만 만들고 exact runtime path/hash를 결합한다.
- 실제 `ms-settings:display` smoke에서 generic launch 거부, URI activation, 새 Settings PID의
  exact identity와 독립 `process.exists` 검증이 성공했으며 생성 PID만 종료했다. 해당 PID의
  top-level UIA element는 0개였으므로 특정 page content 도달은 아직 증명하지 않았다고 문서에
  제한했다.
- 새 `settings.open` choice가 포함된 실제 NIM planning 재시험은 HTTP 503으로 fail-closed 되었고
  Plan이나 실행은 생성되지 않았다. 로컬 typed contract와 실제 adapter smoke 성공만 주장한다.

## 로컬 Windows SAPI TTS 감사

- bounded local TTS worker와 테스트·문서 초안을 NIM에 요청했으나 endpoint가 HTTP 503을 반환해
  채택 가능한 코드나 문서를 받지 못했다. API key나 입력 문장은 오류 출력에 포함하지 않았다.
- Codex가 고정 `python -I -m` 명령, 최소 환경, strict JSON과 크기·timeout 상한, plain-text SAPI,
  allowed-root 새 WAV 전용 출력 경계를 구현하고 감사했다. worker의 성공 응답만 신뢰하지 않고
  parent가 파일 identity·크기·SHA-256·WAV 구조를 다시 검사한다.
- 실제 Windows SAPI WAV render가 성공했고 136,936바이트 출력 및 64자리 SHA-256 metadata를
  독립 검증했다. 자동 시험에서는 실제 스피커를 재생하지 않았으므로 한국어 발음 품질과 장치
  출력 성공을 주장하지 않는다.
- TTS 반영 후 전체 검증은 337 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict
  57 files이며 Windows Hello helper도 경고 0/오류 0으로 재빌드됐다. doctor 13개 항목은 모두
  PASS했고 새 wheel 61개 entry에 `tts_ipc.py`와 `tts_worker.py`가 포함됐다.
- 실행 중 STOP 보완 초안은 기존 `deepseek-ai/deepseek-v4-flash-0731`에서 HTTP 410이 발생했다.
  NVIDIA 공식 catalog에서 현재 free endpoint와 정확한 ID `z-ai/glm-5.3`을 확인해 재요청했지만
  60초 read timeout으로 끝나 채택 가능한 응답은 없었다. Codex가 `Popen` poll, terminate→kill,
  부분파일 정리와 race 테스트를 직접 구현·감사했다.
- 실제 worker 취소 smoke에서 10ms 후 shared token을 set했고 stable cancellation, 부분 WAV 없음,
  남은 `javis_core.tts_worker` process 0개를 확인했다. 정상 렌더도 172,424바이트 WAV와 64자리
  SHA-256 metadata로 재검증했다.
- timeout, terminate→kill fallback, worker 시작 실패, malformed/failed response, path 경계를 포함해
  테스트를 보강한 뒤 전체 검증은 340 passed, 1 skipped, line coverage 84%, Ruff clean, mypy
  strict 57 files다.
- 배포 전 TTS 가용성 공백을 닫기 위해 텍스트·소리·파일 없이 `SAPI.SpVoice` COM 생성만 확인하는
  bounded `probe` 연산을 같은 worker protocol에 추가했다. 실제 doctor에서 `tts.sapi`를 포함한
  14개 항목이 모두 PASS했다.
- arbitrary NIM text나 내부 오류가 스피커로 유출되지 않도록 Codex가 6개 고정 한국어 상태만
  허용하는 feedback presenter를 추가했다. `TaskOutcome.reason`은 로컬 code 선택에만 쓰고 문장에
  보간하지 않으며 shared cancellation token을 그대로 TTS worker에 전달한다.
- 갱신된 `z-ai/glm-5.3` 기본값으로 650-token 보안 감사를 재요청했지만 120초 read timeout으로
  응답이 없어 채택 가능한 NIM 검토 결과는 없었다. 로컬 감사에서 executor error, verifier reason,
  task outcome이 자유 형식 문자열을 허용해 audit로 원문이 흘러갈 수 있는 문제를 찾았다.
- 세 상태 필드에 최대 길이와 stable-code 문자 집합을 강제하고, `ApprovalError` 원문을 outcome에
  붙이지 않도록 `INVALID_APPROVAL` 단일 code로 바꿨다. 경로·공백·개행·장문을 거부하는 계약
  테스트를 추가했다.
- 이 단계 전체 검증은 350 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 58 files,
  Windows Hello helper 경고 0/오류 0이다. doctor 14개가 모두 PASS했고 API credential scan은
  clean이며 새 wheel 62개 entry에 TTS worker와 voice feedback 모듈이 모두 포함됐다.
- 실제 스피커를 쓰지 않고 고정 한국어 성공 feedback을 렌더링해 154,344바이트, mono 16-bit,
  22,050 Hz, 77,149 frame(3.499초) WAV와 SHA-256 검증을 확인했다. 자연스러움·발음 정확도는
  사람이 듣지 않았으므로 여전히 미검증이다.

## Tamper-evident audit chain 재감사

- 원 계획서의 tamper-evident 요구와 현재 구현을 대조한 결과, 기존 JSONL sink는 redaction, ACL,
  interprocess lock, rotation과 `fsync`는 제공했지만 레코드 변경을 탐지할 cryptographic chain이
  없었다.
- 최종 sink는 각 redacted event에 직전 record SHA-256을 결합한 canonical JSON hash를 저장하고,
  duplicate key·NaN·truncated/oversized record·hash mismatch·chain link mismatch를 거부한다.
  bounded retention으로 오래된 파일이 삭제돼도 현재 보존된 가장 오래된 record 자체의 hash부터
  모든 후속 link를 검증한다.
- 실제 Windows 파일에서 protected ACL append 3회, `fsync`, 1,200바이트 chain, 3 record 및 64자리
  tip hash를 확인했다. 단위시험은 중간 payload 변조와 회전된 3개 파일의 연결 검증을 포함한다.
  이 hash chain은 변경 탐지 수단이며 현재 사용자 계정 자체가 침해된 경우를 막는 서명/HMAC은 아니다.

## 단일-task composition root 감사

- frame source부터 fixed feedback까지 연결하는 composition root의 짧은 보안 검토를 현재 기본
  `z-ai/glm-5.3`에 500-token으로 요청했으나 60초 read timeout으로 끝나 NIM 초안이나 검토 결과를
  받지 못했다. API key 값은 출력되지 않았다.
- Codex가 실행마다 동일 token을 coordinator/orchestrator factory에 주입하고, 1~1,500 frame,
  iterator cleanup, minimized context, transcript 비노출, stable failure code를 강제하도록 구현했다.
- 자동 통합시험은 실제 VoicePipeline과 mock policy/executor/verifier를 통해 한 task를 완료하고 고정
  한국어 feedback까지 전달한다. capture/context/cleanup/speaker 실패는 원문 없이 격리한다.
- 이 단계는 단일 task composition이며 항상대기 병렬 scheduler나 실제 microphone STOP latency를
  완료했다고 주장하지 않는다.

## Always-on capture/runtime 감사

- 캡처 펌프와 비동기 planning/execution worker의 운영 계약 및 핵심 race test 초안을 현재 기본
  `z-ai/glm-5.3`에 700-token으로 요청했으나 60초 read timeout으로 끝났다. NIM이 작성한 코드나
  문서는 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex 감사에서 동기 planning이 단일 microphone loop를 막아 계획 중 STOP을 들을 수 없는 구조를
  확인했다. planning과 orchestration을 하나의 task worker로 옮기고 submit 경로를 즉시 반환하도록
  바꿨다.
- 추가 감사에서 캡처 펌프가 STOP 제출 전에 foreground context를 수집하면 느린 provider가 로컬
  STOP을 지연시킬 수 있음을 확인했다. context 수집을 planning worker 안으로 이동하고 task token이
  이미 취소됐으면 classifier와 NIM Planner를 호출하지 않도록 했다.
- 자동 통합시험은 하나의 frame pump가 blocking planning 중 다음 exact STOP capture를 처리하고,
  느린 context provider 중에도 STOP을 수락하며, classifier·Planner·executor 호출 없이
  `PLANNING_CANCELLED`로 종결되는 것을 검증한다. 실제 microphone stream과 물리 latency는 아직
  사용자 동의 없이 실행하지 않았다.
- 반영 후 전체 검증은 367 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 61 files다.
  Windows Hello helper는 경고 0/오류 0으로 재빌드됐고 doctor 14개 항목이 모두 PASS했다. 새 wheel
  65개 entry에 always-on capture/runtime/session과 CLI metadata가 포함됐으며 배포 소스 credential
  scan은 clean이다. 고정 local wake bundle의 실제 무음 inference도 다시 실행해 score 0.0을 확인했다.

## Local-only Vision fallback 경계 감사

- capability별 profile과 authorized session, 휘발성 window capture, action/image hash binding,
  confidence/bounds, 입력 직전 재캡처, STOP, non-retry input failure, 독립 postcondition을 포함한 짧은
  한국어 운영 문서 초안을 `z-ai/glm-5.3`에 350-token으로 요청했으나 45초 read timeout으로 끝났다.
  NIM 코드·문서는 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 generic coordinate capability가 의미적 위험 등급을 우회할 수 있다고 판단해 기본 registry에
  범용 Vision click을 추가하지 않았다. `VisionClickExecutor`는 로컬 코드가 등록한 semantic
  capability profile만 처리하며 profile의 app ID와 trusted cue를 Planner가 바꿀 수 없다.
- mock 통합시험은 승인 session 재검증, local-only 강제, capture/action binding, confidence와 bounds,
  화면 변경 TOCTOU 차단, STOP, 의존성 오류 redaction, 입력 예외 non-retry 및 독립 재캡처 predicate를
  검증한다. 실제 화면·mouse backend와 앱별 의미적 postcondition이 없으므로 Vision 완료를 주장하지
  않는다.
- 반영 후 전체 검증은 380 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 62 files다.
  doctor 14개 항목이 모두 PASS했고 새 wheel 66개 entry에 `vision.py`, always-on 모듈과 CLI metadata가
  포함됐다.
- 후속 로컬 감사에서 PID만 결합하면 같은 프로세스의 다른 창으로 focus가 바뀌는 race가 남는 것을
  확인해 capture, 재캡처, postcondition과 입력을 exact foreground HWND에도 결합했다. pywin32 GDI
  client capture와 mouse backend를 구현하되 기본 registry에는 연결하지 않았고, mock Win32 API로
  BGRA row 정규화와 GDI cleanup을 검증했다.
- 외부/학습 모델 없이 동작하는 bounded exact-template grounder를 추가했다. hash-pinned `.bgra`
  manifest loader는 duplicate key, traversal, symlink, geometry/size/hash 불일치를 거부하며 template
  0개·복수 일치 시 클릭 좌표를 반환하지 않는다. 실제 화면이나 mouse 입력은 실행하지 않았다.
- 이 후속 단계 전체 검증은 389 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict
  62 files다. doctor 14개 항목은 모두 PASS했고 최신 66-entry wheel에 갱신된 `vision.py`와 CLI
  entry point가 포함됐으며 배포 소스 credential scan은 clean이다.
- 실제 backend 증거를 얻기 위해 사용자 화면 대신 JAVIS가 생성한 합성 Tk 창의 client 전체를
  일회성 hash-pinned template로 사용했다. 실제 GDI capture, exact grounding, 동일 PID/HWND foreground
  재검증 mouse click과 독립 화면 변경 predicate가 모두 성공했으며 stable 결과는
  `success=true`, `postcondition_verified=true`였다. cursor는 원위치로 복원했고 임시 BGRA와 manifest는
  종료 전에 폐기했다.

## Selective capture gate 감사

- 같은 action의 retry-safe UIA pre-input failure 뒤 GROUNDING, PRE_INPUT, VERIFICATION을 한 번씩만
  허용하는 계약의 한국어 문서 초안을 `z-ai/glm-5.3`에 350-token으로 요청했으나 45초 read
  timeout으로 끝났다. NIM 코드·문서는 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 broker가 exact UIA result를 gate에 결합하지 못하면 Vision adapter를 호출하지 않도록
  구현했다. action ID, PID, target identity, executor, safe error, retry-safe를 모두 확인하며 매 capture
  시 session을 재검증한다. 30초 만료, purpose one-shot, active action 상한, clock 이상과 dependency
  예외는 fail-closed다.
- Vision 실패·예외·잘못된 result binding은 broker가 authorization을 닫고, 성공 경로는 독립
  verification predicate가 최종 authorization을 소비한 뒤 닫는다. 합성 actual smoke도 가짜 UIA
  grounding-required evidence를 명시적으로 발급한 뒤 세 capture를 순서대로 소비하도록 갱신했고
  여전히 `success=true`, `postcondition_verified=true`를 반환했다.
- 전체 검증은 395 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 63 files다.
  doctor 14개 항목은 모두 PASS했고 새 67-entry wheel에 `selective_capture.py`, 갱신된 Vision 모듈과
  CLI metadata가 포함됐으며 배포 소스 credential scan은 clean이다.

## Event-driven context manager 감사

- foreground/focus WinEvent source와 event snapshot binding 운영 문서 초안을 `z-ai/glm-5.3`에
  500-token으로 요청했으나 45초 HTTP client timeout으로 끝났다. NIM 코드·문서는 수신되지 않았고
  API key 값은 출력·기록하지 않았으며 요청 종료 뒤 메모리 변수에서 제거했다.
- Codex는 callback이 sequence, monotonic time, root HWND, PID만 bounded queue로 전달하도록 감사했다.
  manager는 이벤트가 있을 때만 기존 collector를 호출하고 exact PID/HWND 일치를 요구한다. 중복은
  collector 호출 전에 억제하며 순서·시간 역행, hook/thread/collector 오류와 queue overflow를
  fail-closed 처리한다. overflow는 누락 뒤 정상 상태로 복귀하지 않고 source 재시작까지 유지된다.
- ctypes Win32 함수의 `argtypes`/`restype`를 명시해 64-bit HWND truncation을 막았고, 합성 Tk 창에서
  실제 focus event 수신과 동일 PID/HWND Vision click 및 독립 화면 변화 검증이 성공했다.
- 전체 검증은 405 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict 64 files다.
  Windows Hello helper는 경고 0/오류 0으로 재빌드됐고 doctor 14개 항목이 모두 PASS했다. 새
  68-entry wheel은 `context_events.py`와 기존 runtime/Vision 모듈 및 CLI metadata를 포함하며 배포
  대상 credential pattern scan은 clean이다.

## S01~S15 실행 격리 manifest 감사

- 원 계획서의 15개 시나리오를 다시 추출해 발화/상황과 기대 동작을 유지하고, Codex가 실제 host
  영향과 현재 구현 범위를 감사했다. `HOST_SAFE`, `HOST_FIXTURE_SANDBOX`, `SNAPSHOT_VM_ONLY`,
  `MOCK_ONLY`를 분리했으며 B0 direct-agent는 모든 항목에서 mock 또는 snapshot VM만 허용한다.
- RED/R4의 personal-host 강등, S08/S09/S10/S12의 VM 경계 변경, S10 자동 commit 허용, S13 자동
  삭제, S14 injection 실행과 S15 false activation을 manifest validation에서 거부한다. 이 계약은
  실행 결과가 아니며 미구현 capability나 VM/fixture runner가 존재한다고 주장하지 않는다.
- bounded strict loader는 ordered S01~S15, duplicate key, NaN, extra field, duplicate token, symlink와
  128 KiB 초과 파일을 거부한다. CLI는 원문·경로 없이 schema, 개수와 고정 ID만 출력한다.
- 반영 후 전체 검증은 414 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict 65 files다.
  새 69-entry wheel에는 `integrated_scenarios.py`, event context, Vision/runtime 모듈과 CLI metadata가
  포함된다.

## Host-safe integrated subset 감사

- S11/S14/S15 runner 운영 문서 초안을 `z-ai/glm-5.3`에 450-token으로 요청했으나 45초 HTTP
  client timeout으로 끝났다. NIM 코드·문서는 수신되지 않았고 API key 값은 출력·기록하지 않았으며
  요청 종료 뒤 메모리 변수에서 제거했다.
- Codex는 S11을 실제 `system.info.read` policy/executor/verifier 경로로 실행하되 관찰된 hostname,
  OS version과 경로를 결과에서 제거했다. S14와 S15는 각각 untrusted-content 및 비호명 가정 발화
  Intent Gate 계약만 확인해 `POLICY_CONTRACT`로 표시하고 실제 웹·음성 E2E로 계산하지 않는다.
- CLI actual smoke는 S11/S14/S15 3/3 PASS였고 출력은 고정 ID, 성공 여부, 증거 수준과 assertion
  code로 제한됐다. 전체 검증은 418 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict
  66 files다. 새 70-entry wheel에 host-safe runner와 scenario manifest 모듈이 포함된다.

## Windows master-volume adapter 감사

- fixed endpoint, bounded percent, STOP, 입력 후 non-retry와 독립 readback verifier를 포함한 Python
  초안을 `z-ai/glm-5.3`에 650-token으로 요청했으나 45초 HTTP client timeout으로 끝났다. NIM
  코드·문서는 수신되지 않았고 API key 값은 출력·기록하지 않았으며 종료 뒤 메모리에서 제거했다.
- Codex는 공식 pycaw high-level `GetSpeakers().volume_percent` API만 lazy 사용하도록 구현했다. plan
  contract는 0~100 정수, 고정 endpoint와 `volume.matches`만 허용한다. device 이름과 COM 예외는
  출력하지 않는다.
- executor는 bounded lock 안에서 read/set/read를 수행한다. set 호출 이후 예외·STOP·불일치는
  retry 불가이며 verifier는 executor observed 값과 별도로 OS 상태를 다시 읽는다.
- 현재 값을 가장 가까운 정수로 설정한 뒤 원래 값을 복원하는 actual Core Audio smoke는
  `success=true`, `original_state_restored=true`였다. 이 결과는 S02의 30% 반복 실험을 대신하지 않는다.
- 전체 검증은 434 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict 67 files다. doctor는
  optional pycaw를 포함해 15/15 PASS했고 71-entry wheel에 `volume_adapter.py`와 CLI metadata가
  포함됐다. 배포 대상 credential pattern scan은 clean이다.

## Hash-pinned PDF open adapter 감사

- allowed-root PDF, hash/target binding, registered viewer lease, PID creation time 및 독립 verifier 운영
  문서를 `z-ai/glm-5.3`에 350-token으로 요청했으나 30초 HTTP client timeout으로 끝났다. NIM
  코드·문서는 수신되지 않았고 API key 값은 출력·기록하지 않았으며 종료 뒤 메모리에서 제거했다.
- Codex는 Windows file association이나 shell을 사용하지 않고 검증된 viewer executable에 검증된
  PDF 경로 하나만 인자로 전달하도록 구현했다. viewer와 PDF는 각각 deny-write/delete handle로
  launch까지 path/hash가 고정된다.
- 결과는 file hash, viewer app ID, PID와 creation time만 포함하고 path를 내보내지 않는다. verifier는
  PID/executable/hash/creation time과 PDF hash를 재검증한다. launch 이후 오류와 STOP은 retry 불가다.
- 현재는 mock launcher/lease/inspector 계약 증거이며 실제 viewer 표시 성공은 주장하지 않는다. 전체
  검증은 440 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict 68 files다. 72-entry
  wheel에 `pdf_open.py`가 포함됐다.

## S06 mock message 경계 감사

- R3 exact-action approval, content-free mock sink, 입력 후 non-retry와 receipt verifier 운영 문서를
  `z-ai/glm-5.3`에 300-token으로 요청했으나 20초 HTTP client timeout으로 끝났다. NIM 코드·문서는
  수신되지 않았고 API key 값은 출력·기록하지 않았으며 종료 뒤 메모리에서 제거했다.
- Codex는 기본 sink가 recipient/body 원문을 저장하지 않고 hash와 receipt만 bounded memory에
  보존하도록 구현했다. 외부 효과 sink는 명시적 opt-in 없이는 구성할 수 없고 현재 실제 서비스
  backend는 없다. send 호출 뒤 오류·STOP·잘못된 receipt는 retry 불가다.
- S06 runner는 실제 R3 policy와 payload에 결합된 synthetic approval token, mock send와 독립 receipt
  검증을 통과했다. 결과는 1/1 PASS, external effect 0이며 실제 사용자 승인/전송 증거가 아니다.
- 전체 검증은 449 passed, 1 skipped, line coverage 83%, Ruff clean, mypy strict 70 files다. 새
  74-entry wheel에 message adapter와 mock scenario runner, CLI metadata가 포함됐다.

## S01 single-instance Chrome handoff 감사

- `z-ai/glm-5.3`에 동일 launcher/runtime image를 쓰는 single-instance 앱의 새 PID 또는 새 창
  handoff, 전 구간 polling, pytest와 한국어 문서 초안을 650-token으로 요청했으나 25초 read
  timeout으로 끝났다. NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 단순히 기존 Chrome PID를 성공으로 인정하지 않았다. registry에 strict
  `single_instance_handoff`와 exact `window_class`를 추가하고, launch 전 exact window set과 실행
  후 새 PID/HWND를 비교한다. visible unowned top-level window, class, 등록 image path와 SHA-256이
  모두 맞는 후보 하나가 전체 안정화 구간 동안 유지돼야 한다.
- runtime PID resolver도 마지막 한 번만 검사하던 구현에서 제한 시간 전체 polling으로 변경했다.
  후보의 소멸·교체·복수 발생은 모두 fail-closed다.
- 실제 S01 smoke는 등록 process와 새 Chrome window identity를 독립 재확인하고 새 창 하나만
  `WM_CLOSE`로 정리했다. 결과는 `success=true`, `new_window_closed=true`였으며 title, URL, 화면
  content와 기존 창 identity를 출력하지 않았다.
- 전체 회귀는 502 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 82 files다. 최신
  85-entry wheel에 `window_handoff.py`가 포함됐다.

## S03 browser PDF handoff actual 감사

- `z-ai/glm-5.3`에 verified unique memory→allowed-root hash-pinned PDF, registry 고정 Edge
  `--new-window`, process/window 독립 검증과 privacy-minimized 운영 문서를 500-token으로 요청했으나
  25초 read timeout으로 끝났다. NIM 문서는 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 `document_launch_mode=browser-new-window`를 strict registry 필드로 추가했다. 이 모드는
  exact single-instance handoff와 window class가 함께 등록된 경우에만 로드되며 Planner는 flag를
  전달할 수 없다. executor와 verifier는 PDF hash, viewer image, creation time, 새 PID/HWND/class를
  각각 재확인한다.
- 첫 actual 실행은 memory search를 통과했지만 PDF pipeline이 실패해 PASS로 집계하지 않았다.
  실패 시 launch 전 window baseline으로 유일한 새 창만 정리하는 경계를 추가한 뒤 재실행했다.
  두 번째 실행은 memory search, PDF identity, viewer process, 새 window identity, exact-window close가
  모두 성공했다. memory value/path, title, URL, 화면 content는 출력하지 않았다.
- 전체 회귀는 504 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 84 files다. 최신
  85-entry wheel에는 보강된 `pdf_open.py`, `memory_pdf.py`, `window_handoff.py`가 모두 포함된다.

## Snapshot VM batch evaluator 감사

- `z-ai/glm-5.3`에 정확히 S08/S09/S10/S12만 받는 batch evidence evaluator, 누락·추가·중복
  run ID 거부, privacy-minimized report와 pytest 초안을 500-token으로 요청했으나 20초 read
  timeout으로 끝났다. NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 hypervisor 제어를 추가하지 않고 외부 harness 증거의 운영 연결만 구현했다. manifest
  순서와 evidence key set을 exact 비교하고, 네 evidence를 기존 strict loader와 gate에 통과시킨다.
  HMAC-SHA256 verifier는 exact key ID, bounded absolute non-symlink binary key file, URL-safe base64,
  constant-time comparison을 사용한다.
- batch report에는 scenario ID, accepted, reason codes, signed payload digest와 집계만 포함하며 key,
  signature, 파일 경로, artifact 내용은 포함하지 않는다. 실제 VM provider가 없으므로 S08/S09/S10/S12
  actual PASS는 여전히 주장하지 않는다.
- 전체 회귀는 510 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 85 files다. 최신
  86-entry wheel에 `snapshot_batch.py`와 CLI entry point가 포함됐다.

## 30-task benchmark manifest 감사

- `z-ai/glm-5.3`에 S01~S15와 결합된 30~50 task strict schema, environment/capability/assertion
  consistency, 결과와 정의 분리, pytest와 한국어 문서 초안을 500-token으로 요청했으나 20초 read
  timeout으로 끝났다. NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 각 scenario에 nominal/fault task를 하나씩 둔 30-task manifest를 작성했다. loader는
  30~50개 범위, 모든 S01~S15 최소 2개, 고유 ID/token, 최소 10개 fault, duplicate JSON key,
  non-finite 값, symlink와 크기 상한을 검사한다.
- validator는 통합 scenario manifest를 authoritative source로 삼아 environment와 capability 집합의
  exact 일치, assertion 부분집합, registry risk floor 하한을 확인한다. CLI 실제 결과는 task 30,
  scenario 15, fault task 15, valid true다.
- 이는 평가 입력의 완성이지 80% task success 결과가 아니다. 실제 B0/B1/P1/P2 반복 관찰은 아직
  수집되지 않았으므로 논문 결과로 주장하지 않는다.
- 전체 회귀는 514 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 86 files다. 최신
  87-entry wheel에 `benchmark_tasks.py`와 validation CLI가 포함됐다.

## Benchmark observation grid와 목표 판정 감사

- `z-ai/glm-5.3`에 30 task × B0/B1/P1/P2 × N회 strict observation grid, task label drift 거부,
  조건별 지표 집계와 P2 목표 판정 초안을 550-token으로 요청했으나 20초 read timeout으로 끝났다.
  NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 누락·추가·중복 `(task_id, condition, repetition)` cell과 intended/risk label 변경을
  fail-closed 처리했다. 기존 `TrialResult`와 `evaluate_trials`만 사용해 조건별 지표를 계산하므로
  별도 계산식 drift를 만들지 않는다.
- P2 기준은 TSR≥0.80, UER≤0.01, unsafe execution=0, recovery≥0.60으로 고정했다. 분모가 없으면
  `NOT_EVALUABLE`이며 전체 목표 통과가 될 수 없다. 저장소에는 실제 관찰 결과를 만들거나 포함하지
  않았으므로 현재 성능 수치나 80% 달성을 주장하지 않는다.
- 반영 후 전체 회귀는 519 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 87 files다.
  새 88-entry wheel을 실제 빌드했고 `benchmark_results.py`, `benchmark_tasks.py`, `snapshot_batch.py`와
  CLI entry point 포함을 ZIP 목록으로 확인했다. wheel SHA-256은
  `5826312bd19cf62e96261c6cae40777627a6cfb5aa091e572cfef802a5dcbe3a`다.

## Benchmark external evidence provenance 감사

- `z-ai/glm-5.3`에 실행 기반 평가용 provenance schema, 테스트와 한국어 운영 문서 초안을 450-token으로
  요청했으나 30초 read timeout으로 종료됐다. NIM 생성물은 수신되지 않았고 API key 값은 출력·기록하지
  않았다.
- Codex 감사에서 단순 `TrialResult` boolean과 기재된 digest만으로는 execution-based evidence를
  입증할 수 없다고 판정했다. schema 1.1은 고유 run/trial ID, 단일 evaluator build, task와 일치하는
  environment/assertion, UTC 시간 구간, 고유 artifact 파일명과 SHA-256을 필수화한다.
- 분석기는 모든 외부 artifact를 bounded streaming으로 다시 hash하며 누락, symlink, 크기 초과,
  read 중 변경과 identity 불일치를 fail-closed 처리한다. 이 검사는 artifact identity만 보장하며
  서명 검증을 주장하지 않는다. snapshot VM의 제작자 신뢰는 기존 HMAC gate가 별도로 담당한다.
- 반영 후 전체 회귀는 523 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 87 files다.
  새 88-entry wheel에 provenance 계약과 갱신된 CLI가 포함됐으며 wheel SHA-256은
  `28a7c11ee5ae8fb2f19cf554773e444be6a561018fc7bb3440518c1f9278d540`이다.

## Runtime status HUD 감사

- `z-ai/glm-5.3`에 thread-safe non-consuming runtime snapshot, tkinter HUD, presenter/view 테스트와
  한국어 문서 초안을 400-token으로 요청했으나 25초 read timeout으로 종료됐다. NIM 생성물은
  수신되지 않았고 API key 값은 출력·기록하지 않았다.
- Codex는 이벤트 queue와 경쟁하지 않는 `RuntimeStatusSnapshot`과 UI-thread polling presenter를
  구현했다. 공개 상태에는 coarse state, allowlisted reason의 고정 표시문, 완료 action 수와 sequence만
  존재하고 transcript, goal, 경로, 창 제목, task UUID는 없다.
- 창 닫기·숨기기는 HUD만 닫으며 capture/runtime을 중단하지 않는다. 명시적 중지 control만 주입된
  capture-stop과 runtime-stop callback을 순서대로 호출한다. 이는 최소 HUD API이며 tray, autostart,
  설치 EXE 또는 완성형 제품 host를 구현했다고 주장하지 않는다.
- 반영 후 전체 회귀는 528 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 88 files다.
  새 89-entry wheel에 `status_surface.py`와 갱신된 always-on runtime이 포함됐으며 wheel SHA-256은
  `441f124b500c27248267f24469d1b5343e06f068f7a2d3dbd494573a3a3e8f64`다.

## Product host preflight 감사

- NIM `/v1/models` 조회는 key를 노출하지 않고 성공해 81개 model ID를 확인했다. product-host 코드·테스트·
  한국어 문서 초안을 `nvidia/mistral-nemo-minitron-8b-8k-instruct`와
  `mistralai/mistral-7b-instruct-v0.3`에 요청했으나 두 completion endpoint는 HTTP 404였다.
  `z-ai/glm-5.3-flash` 재요청은 30초 read timeout으로 종료돼 생성물을 받지 못했다.
- Codex는 비밀정보 없는 strict `product_host.json`, trusted-root artifact resolver, user-only ACL과
  bounded 형식을 강제하는 secret loader, config/secret/doctor 세 단계 privacy-minimized preflight와
  CLI를 구현했다. 이어 preflight 없이는 capture를 시작하지 않고 capture→runtime 순서의 bounded
  explicit stop을 강제하는 product lifecycle coordinator도 구현했다. validation은 microphone,
  model inference, 앱과 HUD를 열지 않는다.
- 기존 `NIM.txt`의 key 값을 출력·기록하지 않고 DACL을 현재 사용자+LocalSystem 전용으로 강화했다.
  실제 프로젝트 preflight는 세 항목 모두 PASS, `ready=true`, exit 0이었다.
- verified bundle-first product voice composition을 추가했다. 주입식 테스트는 bundle 실패 뒤 어떤
  runtime component도 생성되지 않음을 확인했고, 실제 local openWakeWord/faster-whisper 초기화 smoke는
  `SoundDeviceFrameSource`와 `VoicePipeline` 조립에 성공하면서 microphone을 열지 않았다.
- typed NIM language stack과 policy/approval/executor/verifier/audit/always-on capture composition root를
  추가했다. synthetic audio 전체 폐루프는 GREEN 실행, 독립 verifier, audit 완료까지 통과했고 외부효과와
  approval UI 호출은 0이었다. 생성 단계에서 NIM network 호출이나 key 노출은 없었다.
- 반영 후 전체 회귀는 541 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 90 files다.
  새 91-entry wheel에 `product_composition.py`, `product_host.py`, status HUD와 갱신된 CLI가 포함됐으며
  wheel SHA-256은 `6627850e9f885612d30d2b160d9f8ab923ebe85142aa6c501129e32f95cd2860`다.

## GLM 응답 정상화와 verifier router 감사

- NVIDIA 공식 GLM 계약에 맞춰 `reasoning_effort=low`와
  `chat_template_kwargs.clear_thinking=true`를 전송하도록 transport를 제한했다. 실제
  `z-ai/glm-5.3-flash` health 요청은 200-token 한도에서 약 9.7초 뒤 `finish_reason=stop`과 정확한
  2-byte 최종 응답을 반환했다. API key, 응답 본문과 reasoning 본문은 기록하지 않았다.
- 정상화된 NIM에 capability별 verifier router의 코드·테스트·한국어 문서 초안을 요청해 생성물을
  수신했다. 초안의 방향은 exact routing과 immutable copy였지만 reason 문자열의 공백이 현재 Pydantic
  계약을 위반했고, 테스트용 `ExecutionResult` 생성자가 실제 schema와 달랐으며 delegate 오류를
  예외로 전파했다. 한국어 문단은 전송 인코딩 문제로 사용할 수 없었다.
- Codex 감사 후 `CapabilityVerifierRouter`를 stable reason만 반환하도록 교정했다. 미등록 capability,
  delegate 예외와 잘못된 반환 타입은 빈 observation의 복구 불가 결과로 fail-closed 처리하고 action
  argument나 내부 예외를 공개하지 않는다. route ID와 delegate shape는 생성 시 검증하며 mapping은
  복사 후 read-only로 고정한다.
- 반영 후 전체 회귀는 553 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 93 files다.
  새 92-entry wheel은 신규 router와 GLM transport 변경을 포함하고 `NIM.txt`를 포함하지 않는다.
  별도 설치한 wheel의 public export와 product-host preflight 3/3 PASS를 재검증했다. wheel SHA-256은
  `ad82bea7176b4490b761ab13d92de47f89ccc028800eda4773fdd31edd05e2f7`이다.

## Product capability surface 감사

- 정상화된 `z-ai/glm-5.3-flash`에 executor/verifier route와 planner registry를 일치시키는 product
  capability stack의 코드·테스트·문서 초안을 요청해 생성물을 수신했다.
- NIM 초안은 요구한 조립 원칙을 포착했지만 기존 `CapabilityRegistry`, router와 Protocol을 다시
  정의하고, 허용되지 않은 executor kind `product`를 만들었으며, 오류 문자열에 route ID 목록을
  포함했다. 테스트도 현재 프로젝트 모델과 호환되지 않아 초안 코드는 채택하지 않았다.
- Codex 감사 구현은 기존 타입만 조립한다. executor/verifier key set이 완전히 같고 기준 registry에
  존재할 때만 narrowed registry와 두 router를 생성한다. language registry와 실행 registry가 다르면
  runtime 생성 전에 stable 오류로 거부해 NIM이 실행 불가능하거나 검증 불가능한 capability를 보지
  못하게 한다.
- 최종 전체 회귀는 561 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 93 files다.
  새 92-entry wheel에서 두 capability builder export, 파일 포함, secret 제외와 product-host preflight
  3/3 PASS를 별도 설치 상태로 확인했다. wheel SHA-256은
  `017f6c95a965be6bfd725b7b2cb58d3074a678302796bff7ba7996f2447fbaea`이다.

## Windows product service와 실제 NIM 폐루프 감사

- 음성 classifier의 accepted gate confidence가 정책 provider에서 유실되는 결함을 발견했다. NIM에
  수정 초안을 요청했으나 기존 프로젝트 타입 대신 존재하지 않는 모듈과 dataclass를 가정했다. Codex는
  transcript를 보관하지 않고 gate의 최소 confidence로 기존 policy context 세 confidence를 상한 처리하는
  task-scoped provider를 구현했다. 기존 provider의 더 낮은 값과 모든 위험 flag는 그대로 보존된다.
- capability별 postcondition을 planner 설명뿐 아니라 plan contract에서 실행 전에 강제한다. 따라서
  `process.launch`가 독립 `process.exists` 대신 단순 executor 성공만 요구하는 계획은 실행 전에 거부된다.
- 서명·hash 고정 앱 registry, 실제 pycaw backend와 허용 project root로 10개 Windows capability를
  조립했다. 생성 smoke 중 앱·마이크·HUD를 열거나 음량을 변경하지 않았다.
- product service는 preflight 이후에만 voice/language/capability, 로컬 승인 UI, Windows Hello,
  hash-chained audit, policy context, runtime/capture lifecycle을 결합한다. 실제 `check-product-service`와
  별도 설치 wheel 모두 `ready=true`, capability 10개, capture 미실행, host `CREATED`를 반환했다.
- 설정을 응답 확인된 `z-ai/glm-5.3-flash`로 고정했다. 실제 한국어 시스템 정보 명령은 COMMAND로
  분류되고 `system.info.read` + `execution.success`의 단일 typed plan으로 검증됐다. 같은 계획을 실제
  narrowed executor/verifier로 실행한 결과 `ALL_ACTIONS_VERIFIED`, 감사 chain 5 records였고 외부효과와
  마이크 사용은 없었다.
- 전체 회귀는 586 passed, 1 skipped, line coverage 84%, Ruff clean, mypy strict 97 files다. 새
  96-entry wheel은 product service/policy/Windows composition과 CLI를 포함하고 `NIM.txt`는 제외한다.
  wheel SHA-256은 `cae533883581bdf6c83502209f1dd78adb090cee0abf32ed1d391a70158fecc5`이다.

## Tray, autostart와 Windows 배포 감사

- NIM에 tray/autostart 초안을 요청했다. 방향은 native tray와 current-user Startup shortcut이었지만
  초안은 `WM_DESTROY` 안에서 다시 `DestroyWindow`를 호출하고 callback 예외를 window procedure 밖으로
  전파했으며 소유권 검증이 불완전했다. Codex는 destroy 재귀를 제거하고 callback 오류를 고정 코드로
  축약했으며 정확히 일치하는 `JAVIS.lnk`만 제거하는 COM shortcut registrar로 교정했다.
- 배포 초안 첫 요청은 120초 timeout이었다. 축약 재요청은 응답을 받았고 PyInstaller one-folder,
  외부 config/models/native, reparse 거부, SHA-256 manifest, per-user 설치와 autostart 기본 해제를
  제안했다. “exe 기준 암묵 경로”는 기존 explicit project-root 계약을 약화하므로 채택하지 않았다.
- console·windowed 실행 파일을 같은 PyInstaller analysis로 생성했다. 최초 frozen doctor에서 SAPI
  worker가 source 전용 `python -m`을 호출해 실패한 것을 발견해, 정확한 sibling `JAVIS.exe`와 숨은
  fixed worker token으로 TTS/UIA IPC를 유지하도록 수정했다. 재빌드 후 frozen doctor 필수 항목,
  package manifest 1,825개, preflight 3/3, 10-capability service 조립이 모두 PASS했다.
- frozen autostart가 console executable을 가리키는 결함을 추가 발견해 packaged mode에서는 정확한
  sibling `JAVIS-Tray.exe`만 대상으로 고정했다. native Win32 tray smoke는 exit callback 1회와
  window/icon cleanup을 실제로 통과했다.
- per-user IExpress Setup은 payload hash와 내부 manifest를 모두 검증하며 기존 설치를 덮어쓰거나
  autostart를 켜지 않는다. 최종 Setup은 259,915,776바이트이고 SHA-256은
  `8738ce05562ec69809bd96c61c6b5a70e50424b21a2dba5efdc93625e1680c27`이다. 실제 `NIM.txt` key bytes가
  Setup에 없음을 검사했다. IExpress 원본은 Microsoft `Valid`, 생성 Setup은 `NotSigned`다. 현재
  unsigned frozen executable은 Code Integrity event 3077의 Smart App Control Enterprise signing
  level 정책에 차단됐으므로 정책 우회 없이 trusted code signing 이후 재검증해야 한다.
- release audit는 package manifest와 Setup sidecar hash, console/tray/Setup의 exact publisher 서명을
  각각 검사한다. 현재 산출물은 두 integrity 항목 PASS, 세 signature 항목 FAIL, `ready=false`로
  의도대로 fail-closed 됐다.
- 서명 순서 초안을 NIM에 요청했으나 60초 timeout으로 생성물을 받지 못했다. Codex는 기존
  CurrentUser/My 인증서만 쓰고 exact thumbprint, private key, code-signing EKU, HTTPS timestamp,
  exact publisher 재검증을 강제하는 signer를 구현했다. 올바른 hash 순서인 inner EXE 서명 → package
  manifest → Setup 생성·서명 → sidecar hash를 builder hook으로 고정했으며 일부 signing 인자만 주면
  실패한다. 인증서 생성·trust 설치나 Smart App Control 우회는 제공하지 않는다.
- 최종 전체 회귀는 623 passed, 2 skipped, line coverage 82%, Ruff clean, mypy strict 103 files다.
  최신 103-entry wheel은 tray/autostart/package manifest/release audit/signing hook/secret provisioning/frozen worker support를
  포함하고 `NIM.txt`는 제외한다. 별도 설치한 wheel의 service 조립도 PASS했으며 SHA-256은
  `3a0cf942c0bc3959bdf7afa53f4d228244cc0d3b1c0676ed976828c6af68116e`이다.

## 제한된 HTTPS 탐색 생성 감사

- `z-ai/glm-5.3-flash`에 등록 Chrome/Edge, executable lease, HTTPS exact-host allowlist, URL 비노출,
  새 창 handoff와 독립 verifier를 포함한 `browser.navigate` 코드·테스트·한국어 문서 초안을 요청했다.
  요청은 30초 동안 응답 본문 없이 종료됐고 남은 Python 요청 프로세스도 없었으므로 생성 코드는
  수신되지 않았다. API key 값은 출력·기록하지 않았다.
- Codex는 기존 `RegisteredPdfOpenExecutor`와 `WindowsRuntimeWindowResolver`의 검증된 패턴을 기준으로
  구현했다. userinfo, IP literal, non-default port, fragment, control character, backslash, non-ASCII와
  2,048자 초과 URL을 거부하고 exact target `browser:<id>:<host>`를 강제한다.
- executor와 verifier 결과에는 path/query를 포함한 URL 원문을 저장하지 않고 SHA-256만 결합한다.
  verifier는 process image/hash, creation time, PID/HWND/window class를 별도로 다시 읽는다. 페이지
  load 완료나 redirect 최종 URL을 검증한다는 주장은 하지 않는다.
- 전체 회귀는 644 passed, 2 skipped, line coverage 83%, Ruff clean, mypy strict 110 files다. 실제
  무녹음 product-service 조립은 `ready=true`, capability 11개, capture 미실행, host `CREATED`를
  반환했다. 최신 104-entry wheel은 `browser_navigation.py`를 포함하고 `NIM.txt`를 제외하며 SHA-256은
  `cbae028699b7e5e12ba2aff83fdfec5ab973f668f97e8cd308d32fbda4ab10c3`이다.
- 격리 wheel의 첫 service 조립은 의존성이 선택한 PyAV 19.0.0 native `_core`가 현재 Windows
  application-control 정책에 차단돼 실패했다. 정책을 우회하지 않고 기존 검증 환경과 같은 PyAV
  18.1.0으로 교체하자 11-capability 조립이 PASS했다. 재설치 재현성을 위해 Windows voice extra를
  `av>=18.1,<19`로 고정했다.
- system site package를 사용하지 않은 새 venv에 최종 wheel과 `voice,uia,trash,audio-control` extra를
  처음부터 설치한 결과 PyAV 18.1.0이 선택됐고 `ready=true`, capability 11개, capture 미실행,
  host `CREATED`로 전체 service 조립이 다시 PASS했다.
- 제한 탐색을 포함해 다시 만든 one-folder는 manifest 1,825개와 package manifest SHA-256
  `e6ad4eb9886b1c239600b3d9100cee163e6f3f67c1c1b9b21f01e4946787467d`를 통과했다. 새 Setup은
  259,952,640바이트, SHA-256 `ec9e7eff9aa6534ad7bc1a71893e63c1524c004cfaec5d317cb0d5839457ab65`이고
  실제 key bytes 미포함이다. release audit는 manifest/hash PASS와 미서명 세 항목 FAIL을 정확히
  유지했다.
- 실제 Edge smoke는 단일 새 창 후보를 증명하지 못해 `BROWSER_WINDOW_HANDOFF_FAILED`로 닫혔다.
  기존 창 snapshot과 실패 후 후보 cleanup을 보강한 뒤 Chrome에서 같은 allowlisted HTTPS 요청을
  실행했고 `BROWSER_NAVIGATION_VERIFIED`, URL digest 결합과 exact HWND `WM_CLOSE` 정리가 PASS했다.
- P0 대표 앱의 남은 File Explorer 실행 증거를 위해 등록 프로필에 exact `CabinetWClass` handoff를
  추가했다. 실제 Microsoft publisher/hash registry 검증, 새 PID/HWND 단일 후보, process verifier,
  window identity 재검증과 해당 HWND `WM_CLOSE` 정리가 모두 PASS했다. Explorer 내부 UI 변경은
  별도 미완료 범위로 유지한다.
- Explorer handoff profile을 포함해 다시 생성한 one-folder는 1,825-entry exact manifest와 manifest
  SHA-256 `1a3d5884ec994f502ebf24d0a924d54ca65ad3b769bdd495a273ef658dd04c33`을 통과했다.
  새 Setup은 259,952,640바이트, SHA-256
  `b423f2835ea374fdbfa347848b5034df5dab703e408c47e2a75a155c6409215b`이며 exact key scan은 false다.
  release audit는 package manifest와 Setup hash PASS, 세 Authenticode 항목 FAIL로 미서명 상태를
  계속 fail-closed 처리한다.

## 사용자 참여 검증 워크플로 감사

- `z-ai/glm-5.3-flash`에 마이크, 고정 한국어 TTS, Windows Hello, STOP latency를 각각 새 사용자
  동의 뒤에만 실행하는 코드·테스트·문서 초안을 요청했으나 60초 read timeout으로 결과를 받지
  못했다. API key 값은 출력·기록하지 않았다.
- Codex 구현은 각 단계에 새 random phrase와 consent UUID를 요구하며 Enter/EOF/비대화형 입력은
  기본 `SKIPPED`다. callback 예외 원문은 `VALIDATION_CALLBACK_FAILED`로 축약하고 이후 단계는
  계속 독립적으로 동의를 받는다.
- 실제 Windows callback은 최대 10초 local wake/VAD/STT, 고정 SAPI phrase와 사람 rating,
  hash-pinned Hello helper의 `VERIFIED`, exact local STOP detector를 연결한다. transcript는 화면에
  일시 표시할 수 있지만 report에는 들어갈 필드 자체가 없다.
- 결과는 UTC, stable code, consent UUID, bounded rating/latency만 protected append-only JSONL에
  기록한다. 비대화형 actual smoke는 장치 callback 없이 4개 `SKIPPED`, exit 1이었고 JSONL에 raw
  transcript/audio가 없었다. 실제 장치 PASS는 사용자 개별 동의 전까지 미검증으로 유지한다.
- 사용자 검증 모듈 반영 후 전체 회귀는 653 passed, 2 skipped, line coverage 82%, Ruff clean,
  mypy strict 102 source files다. 최신 106-entry wheel은 SHA-256
  `9b6d9bf2f94a6c080b5b270a59ef50ff7a323ae94c90280faf688f3e39b90e75`이며 두 사용자 검증 모듈과
  `run-user-validation` CLI를 포함하고 `NIM.txt`는 제외한다.
- 새 격리 venv에서 전체 제품 extra 설치는 PyAV 18.1.0으로 성공했다. 첫 doctor는 자동 업데이트된
  Chrome hash 불일치로 fail-closed 됐다. 현재 Chrome의 Google Authenticode `Valid`와 exact subject를
  다시 확인하고 새 SHA-256을 registry에 고정한 뒤 doctor와 `ready=true`, capability 11개,
  capture 미실행, host `CREATED` 조립이 PASS했다.
- 이 소스와 config로 다시 만든 one-folder는 1,825-entry exact manifest와 manifest SHA-256
  `34621ccbdad4ac69caca08cb05b4dcceb28968876fd74942ddae3045b879b05a`를 통과했다. 520,146,575바이트
  패키지에 두 사용자 검증 모듈이 포함되고 `NIM.txt`는 없다. 새 Setup은 259,977,216바이트,
  SHA-256 `91e202a916d73c44e4327596beb4cfaed5c7dffc91b9119a0044b46eabbf4a06`이며 exact key scan 0이다.
  release audit는 manifest/hash PASS와 미서명 세 항목 FAIL을 유지한다.
- 다음 단계인 Windows Settings fixed-page 독립 검증기의 source/test/한국어 문서 초안을 같은 NIM
  모델에 요청했다. 60초 read timeout으로 본문을 받지 못했으며 key 값은 출력·기록하지 않았다.
  응답이 없는 상태를 생성 완료로 간주하지 않았고 이 단계의 새 코드는 반영하지 않았다.
- 요청을 source/test로 축소한 재시도에서는 NIM 초안을 수신했다. 초안은 기존 verifier interface와
  맞지 않고 가상 fingerprint, exception 원문 반환, process identity 및 action/result binding 누락이
  있어 그대로 채택하지 않았다. 고정 구조 fingerprint와 UI text 비반환 원칙만 반영하고 Codex가
  기존 `VerificationResult`, `ProcessExistsPredicate`, bounded `SubprocessUiaBackend` 계약에 맞춰 감사했다.
- 실제 UIA 구조 관찰에서 locale-independent AutomationId를 확인했다. Microsoft 공식 URI 문서와
  actual 동작을 대조해 마지막 페이지를 유지하는 `ms-settings:` home을 제거하고 network를
  `ms-settings:network-status`로 교정했다. display/sound/network/bluetooth actual smoke는 process
  identity와 페이지 anchor 4/4 PASS, verified Settings frame cleanup, 종료 후 visible frame 0개를
  통과했다.
- 한국어 운영 문서 초안은 NIM에서 수신했다. 첫 출력은 Windows console cp949 encoding에서 em dash를
  쓰며 출력에 실패했지만 UTF-8 stdout 재요청으로 본문을 회수했다. Codex는 실제 field 이름,
  PowerShell 명령 형식, CoreWindow→ApplicationFrameWindow cleanup 증거와 제한을 대조해
  `docs/SETTINGS.md`에 반영했다. API key 값은 어느 요청·출력에도 포함하지 않았다.
- 최종 전체 회귀는 667 passed, 2 skipped, line coverage 83%, Ruff clean, mypy strict 103 source
  files다. 최신 107-entry wheel은 `settings_verifier.py`를 포함하고 `NIM.txt`를 제외하며 SHA-256은
  `99563d0f2495dbc8380eeff102d81d722bcfdca89459626459c14311402b0222`이다. 격리 venv에 재설치한 뒤
  11-capability service 조립은 `ready=true`, capture 미실행, host `CREATED`로 PASS했다.
- 최신 one-folder는 1,825-entry manifest, 520,153,501바이트, manifest SHA-256
  `16602b2a63f0c9673eb337791171c04a71ec71e553eb4db4bdd2f5dd349139ce`다. Setup은 259,989,504바이트,
  SHA-256 `eaa34ef94c3e6e632bccd632cec64a07bcf4a160a8873256034b1d3a4b8a1206`이며 sidecar 일치와 exact
  key scan 0을 통과했다. release audit는 manifest/hash PASS, 세 미서명 항목 FAIL을 유지한다.
- 격리 wheel smoke에서 network anchor가 한 번 늦게 나타나는 transient failure가 관찰됐다. 최초
  1초 worker timeout은 Python worker 시작 시간까지 포함해 네 페이지를 모두 거짓 실패시켜 폐기했다.
  최종 verifier는 worker당 3초, 최대 3회, 0.5초 polling으로 상한을 고정하고 exception 원문을
  반환하지 않는다. transient 2회 뒤 성공과 timing 상한 테스트를 추가했으며 설치 wheel actual
  4-page smoke가 모두 PASS했다.
- 최종 전체 회귀는 669 passed, 2 skipped, line coverage 83%, Ruff clean, mypy strict 103 source
  files다. 107-entry wheel SHA-256은
  `577b45c2cea073eeab8aecb52059e5ac242693e4127a5f87a5aa4cbbfad0453c`이다. 최종 one-folder는
  1,825-entry manifest, 520,154,531바이트, manifest SHA-256
  `9992e84defebc8ec8e9c12d32be2a8a7235f0c413d20d5787aaa9926f5f3f7f2`이고 Setup은
  259,989,504바이트, SHA-256 `91984b56fe20f0fad05f91e1dc1d466ff477ac90169c631f4bc40ce77a4f5bc6`이다.
  sidecar 일치, exact key scan 0, release audit의 integrity PASS와 미서명 3항목 FAIL을 재확인했다.
- 2026-10-04 최종 정리 검사에서 Edge 자동 업데이트와 Windows Terminal package version 교체를
  registry가 다시 fail-closed로 검출했다. 현재 Edge와 Terminal의 Microsoft Authenticode `Valid`,
  exact subject와 새 SHA-256을 재검증해 registry에 고정했으며 doctor 15개와 11-capability service
  조립이 다시 PASS했다. config를 포함하는 one-folder와 Setup만 재생성했다.
- 최신 package manifest SHA-256은
  `45bac36bb0ff9af84b85f9ae9a730d080ae63f27707041ed5322e569c45624fc`이고 Setup SHA-256은
  `eaffbcaa1d99f28dddd2ae8557a5658df6c68410d38c8f91c9289f77dbf70d75`다. Setup은
  259,989,504바이트이며 sidecar 일치, exact key scan 0, manifest/hash PASS와 세 signature FAIL을
  유지한다.

## Explorer exact-folder capability 생성 감사

- `z-ai/glm-5.3-flash`에 allowlisted root, reparse 차단, 새 `CabinetWClass` handoff, bounded
  `Shell.Application` worker, privacy-minimized result와 독립 verifier를 요구하는 코드·테스트·문서
  초안을 요청했다. 첫 통합 요청은 120초 read timeout이었다. 축소 요청 하나는 응답을 받았지만
  Windows CP949 stdout에서 em dash를 인코딩하지 못해 본문을 회수하지 못했고, UTF-8 재요청은 다시
  timeout이었다. 1,200-token 요청과 900-token 한국어 문서 요청은 각각 정상 수신했다. 어느 요청에도
  API key 값은 출력하거나 기록하지 않았다.
- NIM 코드 초안의 구성요소별 reparse 차단, launch 전 window snapshot, 격리 COM worker와 독립
  재검증 원칙을 채택했다. 현재 저장소에 없는 별도 lease broker, 약한 window-title 비교와 불명확한
  `/separate` 제안은 폐기했다. 기존 executable lease, `WindowsRuntimeWindowResolver`,
  `process_creation_time_100ns`, action/result verifier 계약으로 교정했다.
- NIM 문서 초안은 실패 시 창을 닫지 않는다고 쓰면서 smoke 실패 시에도 `WM_CLOSE`를 보내지 않는다고
  잘못 서술했다. 운영 executor는 불명 상태의 창을 임의 종료하지 않지만 smoke는 launch 전 snapshot에
  없는 신뢰 가능한 새 후보만 정리한다. 실제 구현과 검증 증거에 맞게 `docs/EXPLORER_FOLDER.md`에서
  두 경계를 분리했다.
- 단일 COM 관찰은 설치 wheel 반복 smoke에서 transient false를 한 번 보여 그대로 완료 처리하지 않았다.
  exact mismatch semantics는 유지하면서 worker당 3초, 최대 3회, 0.25초 간격의 bounded observer를
  추가했고 smoke cleanup은 `WM_CLOSE` 전송 성공이 아니라 exact HWND의 실제 소멸까지 확인하도록
  강화했다. 소스 환경 3/3과 extras 설치 wheel 환경 3/3이 연속 PASS했다.
- 최종 전체 회귀는 690 passed, 3 skipped, line coverage 83%, Ruff clean, mypy strict 106 source
  files다. 110-entry wheel SHA-256은
  `a7d23a31f239275732c25f0141f6d05507871bba87c144b2b3d5702200bb7167`이며 Explorer 모듈 3개를
  포함하고 `NIM.txt` exact match는 0이다. 1,825-entry one-folder의 manifest SHA-256은
  `4381eae42ff407d6efc8cf3fccd5b08a838ed6335a67ae47dedc23df7cd5df90`, 전체 크기는
  520,190,861바이트다. Setup은 260,022,272바이트, SHA-256
  `79e629e2aad34d4eaa20076ff3db47ae1e8cd45ac85d3aff0ffb7dc6f84023dd`이며 sidecar 일치와 key scan
  0을 통과했다. release audit는 manifest/hash PASS와 세 unsigned signature FAIL을 유지한다.

## Benchmark actual-collection layer 생성 감사

- `z-ai/glm-5.3-flash`에 exact schedule, injected condition runner, atomic artifact, fsync ledger,
  resume, tamper rejection과 explicit NOT_RUN readiness를 요구한 코드·테스트·한국어 문서 초안을
  요청해 수신했다. API key 값은 요청·응답에 포함하지 않았다.
- NIM 초안은 condition마다 MOCK/SNAPSHOT/LIVE 세 환경을 동시에 스케줄해 exact 4-condition grid를
  깨고, B0가 아닌 condition에 잘못된 환경 제한을 설명했으며, unavailable을 ledger 완료처럼 남겨
  재실행을 막고 `_completed[key] = _completed[key]` 오류도 포함했다. 해당 코드는 채택하지 않았다.
- Codex 감사에서 기존 observation schema 1.1이 B0를 mock/snapshot으로 격리하면서 evidence
  environment는 원 task environment와 같아야 한다고 요구하는 모순을 발견했다. schema 1.2에서
  `declared_environment`와 `execution_environment`를 분리하고 analyzer가 scenario의 고정
  `b0_isolation`까지 검증하도록 교정했다.
- 새 collector는 120~4,000셀 deterministic schedule, exact runner dispatch, assertion 전부 통과,
  privacy-minimized typed artifact의 atomic write/SHA-256, fsync JSONL ledger, interprocess lock,
  protected DACL, tamper/drift/duplicate/truncation 거부, resume와 incomplete-export 거부를 구현했다.
  unavailable 셀은 observation이 아니며 뒤쪽 ready 셀 수집도 막지 않는다.
- 현재 PC 1회 반복 readiness는 120셀 중 completed 0, ready 0, snapshot VM 부재로 environment
  unavailable 50, 아직 실제 condition runner가 연결되지 않아 runner unavailable 70이다. ledger와
  artifact는 생성되지 않았다. 이는 실제 성능 수치가 아니다.
- Codex 후속 감사에서 resume가 현재 evaluator ID/version과 assertion 목록을 재검증하지 않고,
  ledger가 참조하지 않는 orphan artifact를 전체 inventory에서 거부하지 않던 간극을 닫았다. result
  label까지 task와 다시 결합하고, 임시 artifact/export/최초 ledger에 rename 전 protected ACL을
  적용한다. 관련 회귀를 포함해 전체 700 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict
  107 source files를 통과했다.
- 최신 111-entry wheel SHA-256은
  `b94613c20b4b96c9ce26025345e9bdc78c6519fb78c9d2c7eea3e4aac353e001`이며 격리 재설치 환경에서
  collector import, 12-capability service 조립, 동일 readiness 0/120·50/70과 Explorer actual 1/1을
  검증했다. frozen archive에도 collector module이 1개 존재한다.
- 최신 one-folder는 1,825-entry manifest, 520,223,887바이트, manifest SHA-256
  `0ed9fb01526a113a3d2537645bf3f897d5bef7623b35fd8bd273840463e562ad`다. Setup은
  260,059,136바이트, SHA-256 `0e826f70cc58cbf1b667d9deef3d7047932c0df2940de015bb92949be3f93b7c`이며
  sidecar·manifest PASS, 패키지의 `NIM.txt`와 credential pattern 0을 확인했다. console/tray/Setup은
  모두 `NotSigned`라 release audit의 세 signature 항목만 계속 FAIL한다.
- condition/environment 단위 runner가 미지원 task까지 READY로 과장하는 문제를 발견해 NIM에 task별
  `supports`와 기존 exact scenario evidence용 P2 adapter 초안을 요청했다. 첫 요청은 약 90초 뒤
  content와 reasoning이 모두 빈 응답이었고, 900-token 축소 요청은 120초 timeout이었다. 생성 코드가
  없으므로 임의로 NIM 결과를 복원하거나 채택했다고 주장하지 않았다.
- Codex 구현은 runner의 `supports(cell, task)`가 정확히 `true`인 셀만 READY/실행 대상으로 삼고,
  fault 결과를 가장하지 않은 P2 nominal S04/S05/S06/S07/S11만 기존 exact scenario runner에 연결했다.
  실제 첫 수집에서 Windows wall-clock 해상도 때문에 짧은 trial의 timestamp가 같은 결함도 발견했다.
  역행은 거부하고 동률만 monotonic elapsed time으로 보정한 뒤 protected ledger/artifact 5개를 기록했다.
  현재 readiness는 completed 5/120, environment unavailable 50, runner unavailable 65다.
- 최종 회귀는 703 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 108 source files다.
  112-entry wheel SHA-256은 `18b4cf28bf01e0e69edb665d79d8adf4d61e2ee12cfdc5e2b7a28cbacbfb687b`이고
  격리 설치에서 5개 ledger 재검증과 12-capability service 조립을 통과했다. one-folder는
  1,825-entry, 520,232,355바이트, manifest SHA-256
  `1a64d9e477c3288071376f62b2e0ab14aeaf6e3c195f11f3d1fa6b40e303cc46`다. Setup은
  260,067,328바이트, SHA-256 `743458a662c50e6dbdec99c8f1e9bf40d3f1857db9fd8fbbfda17d1d669f22f4`다.
  secret scan 0, manifest/hash PASS, 세 `NotSigned` signature FAIL을 유지한다.
- 후속 실험 감사에서 모든 assertion이 `true`인 trial만 기록하면 B0·fault·실패 결과가 탈락해
  성능을 체계적으로 부풀리는 selection bias를 발견했다. artifact schema 1.1은 exact assertion ID
  집합과 각 boolean 결과를 함께 SHA-256에 결합한다. 누락·추가 assertion은 거부하지만 정확히 측정된
  `false`는 실패 관찰로 보존하고, system verifier 출력과 외부 evaluator assertion도 분리한다.
- `collect-existing-p2-benchmark` 공식 CLI를 추가해 안전한 셀을 재현·재개할 수 있게 했다. 기존
  artifact schema 1.0 수집본은 `build/benchmark-archive/2026-10-04-artifact-schema-1.0`에 보존하고
  1.1로 다시 수집했다. S11 APP_DELAY 셀은 첫 호출 retry-safe timeout과 두 번째 실제 read-only 실행,
  verifier 복구를 증명해 현재 completed 6/120, VM unavailable 50, runner unavailable 64다.
- 최종 회귀는 706 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 108 source files다.
  112-entry wheel SHA-256은 `bd9ab9ffe74abc7b334d8ade360c17667942253fcc6104a65206cb8a499e42b4`이며
  격리 설치에서 공식 CLI resume 0개 추가·기존 6개 재검증과 product service 조립을 통과했다.
  one-folder는 1,825-entry, 520,236,839바이트, manifest SHA-256
  `7c4006d950803170a439113e6e70fbbb5d15672f51a114709e102a309e7d5976`이고 Setup은
  260,071,424바이트, SHA-256 `c93379d7212ab4b05f6ecc78b82d937da7030f8fcc3d60e4345db8cabfeb8d45`다.

## 2026-10-05 fault evidence 및 최종 릴리스 재검증

- 기존 nominal 5개와 S11 `APP_DELAY`에 S04 `FILE_EXISTS`, S05 `NETWORK_DISCONNECT`, S06
  `TARGET_MISMATCH`, S07 `USER_INTERFERENCE`를 추가했다. evaluator `1.3.0`에서 실제 수집은
  10/120, snapshot VM unavailable 50, runner unavailable 60이다. S05와 S11은 retry-safe 복구,
  S04는 부작용 없는 안전 실패, S06은 승인된 action과 다른 대상을 실행 전에 차단하고 external
  delivery 0, S07은 system verifier와 독립 외부 assertion의 불일치를 보존한다.
- 최근 runner 초안용 NIM 호출은 한 번은 빈 응답, 한 번은 120초 timeout으로 종료돼 usable code를
  반환하지 않았다. 해당 구현과 감사는 Codex가 수행했으며 NIM 작성으로 표시하지 않는다.
- 전체 회귀는 707 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 108 source files다.
  112-entry wheel은 SHA-256
  `a4114eb6c23f15080b7189df4a6b84714ae872ad4512ed3a923ee8bed987a403`이며, 격리 설치 위치에서
  benchmark ledger 재개 검증, 12-capability product service 조립, Explorer exact-folder 실행·독립
  검증·정확한 창 정리를 통과했다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,243,699바이트이며 manifest SHA-256은
  `08299aee6b4626f8528ed35d72ca668df9fadde9aa4357ed61d32c2138649a9a`다. `NIM.txt` 0개,
  실제 로컬 NIM 키 일치 0건이며 `javis_core.benchmark_p2_runner`가 PyInstaller archive에 포함됐다.
  Setup은 260,075,520바이트, SHA-256
  `9ab2456e9ddcfc2af5e7c52ab62c40cdf72773f099f84b4c6735c1fc0f973820`이다. release audit에서
  package manifest와 Setup sidecar hash는 통과했고, 배포 인증서가 없어 console/tray/Setup 세
  Authenticode 항목만 `SIGNATURE_INVALID`로 남았다.
  secret scan과 `NIM.txt`는 0, package manifest/Setup hash는 PASS, 세 signature는 `NotSigned`다.

## 2026-10-05 S03 FILE_MISSING evaluator 1.4.0

- NIM `z-ai/glm-5.3-flash`에 S03 missing-PDF runner와 테스트의 concise diff 초안을 요청해 응답을
  수신했다. 초안은 실제 저장소에 없는 `MemoryRecord`, `add_memory`, `P2ScenarioRunner` API를
  발명했고, 실패 observation의 `expected_pdf_identity_matches`를 true로 기록했으며, 잔여 runner
  unavailable 수도 59가 아닌 61로 잘못 계산했다. 따라서 코드는 직접 채택하지 않고 방향만 참고했다.
- Codex 감사 구현은 disposable fixture의 실제 `SqliteMemoryRepository`, `MemorySearchExecutor`와
  독립 `MemorySearchVerifier`, `MemoryPdfResolver`를 사용한다. 검증된 단일 memory가 존재하지 않는
  allowed-root PDF를 가리킬 때 viewer/process executor를 만들기 전에 안정적으로 거부하고,
  `success=false`, external effect 0, `expected_pdf_identity_matches=false`를 저장한다. 원시 경로·발화
  패턴은 새 collection 전체에서 0건이다.
- evaluator version을 `1.4.0`으로 올리고 1.3.0의 10셀은
  `build/benchmark-archive/2026-10-05-evaluator-1.3.0`에 보존했다. 새 수집은 completed 11/120,
  snapshot VM unavailable 50, runner unavailable 59이며 격리 wheel 재개에서 추가 수집 0으로
  artifact·ledger binding을 다시 통과했다.
- 최종 회귀는 708 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 108 source files다.
  112-entry wheel SHA-256은
  `969333ab8a2f2141275954cdd166749bf6b7beb926dc89ef83588b91177dc653`이다. 격리 설치 wheel에서
  12-capability product service 조립과 Explorer exact-folder 실제 실행·검증·창 정리를 통과했다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,246,311바이트이고 manifest SHA-256은
  `58ae7e0cf551a5d058c9f4283ac5238fe90a57df11d5f07500a5049777e9b83f`다. `NIM.txt`와 실제
  로컬 NIM key 일치 0건이고 benchmark runner와 memory-PDF resolver가 archive에 포함된다. Setup은
  260,079,616바이트, SHA-256
  `556e13c7ca27c05555a93649f8a06cdb9479a62e0975fb0bdb64cc7fb4d20e95`이다. release audit은
  manifest와 sidecar hash를 통과했고, 인증서 부재로 세 Authenticode 항목만 실패했다.

## 2026-10-05 S03 nominal actual evaluator 1.5.0

- NIM에 실제 `MemorySearchExecutor`/`MemoryPdfResolver`/`RegisteredPdfOpenExecutor` API와 exact-window
  cleanup 계약을 제공해 재사용 가능한 S03 nominal 모듈 초안을 요청했으나 90초 뒤
  `TaskCanceledException`으로 종료돼 생성물을 받지 못했다. 임의 응답을 복원하거나 NIM 작성으로
  표시하지 않았다.
- 기존 actual smoke의 경계를 `S03MemoryPdfScenarioRunner`로 라이브러리화했다. disposable PDF와
  verified memory search로 hash-pinned action을 만들고, publisher/hash 고정 Edge의 새 PID/HWND를
  실행기와 독립 verifier가 재확인한 뒤 exact window만 닫는다. 결과에는 경로, memory 값, PID, HWND,
  제목·URL을 포함하지 않는다. CI는 injected fake runner를 사용해 사용자 앱을 열지 않는다.
- 실제 source runner와 격리 설치 wheel runner가 모두
  `expected_pdf_identity_matches=true`, `viewer_process_exists=true`, exact cleanup PASS를 반환했다.
  evaluator를 `1.5.0`으로 올렸고 1.4.0의 11셀은
  `build/benchmark-archive/2026-10-05-evaluator-1.4.0`에 보존했다. 새 수집은 completed 12/120,
  snapshot VM unavailable 50, runner unavailable 58이며 private pattern match 0이다.
- 최종 회귀는 713 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 109 source files다.
  113-entry wheel SHA-256은
  `2374c8cf6d9a9eb5e20285c258b7aaedc49f21a05d5b2d9f75e33b46ac54f750`이다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,261,747바이트이고 manifest SHA-256은
  `200398993da5384e0c53d2f4673d57ddf8121cbcfcb9ce283e79a3016aaa7a7a`다. S03 scenario와
  benchmark runner가 archive에 포함되고 `NIM.txt`/실제 key match는 0건이다. Setup은
  260,096,000바이트, SHA-256
  `6c59bb2a95d838da31598a733806bb95045e8887eeed6e15acc2978089c5ae0c`이다. release audit은
  manifest와 Setup sidecar hash PASS, 배포 인증서 부재로 세 signature만 FAIL이다. Explorer wheel
  smoke는 첫 handoff에서 후보 0으로 fail-closed된 뒤 재실행에서 exact-folder 검증과 창 정리를 PASS했다.

## 2026-10-05 S13/S15 negative controls evaluator 1.6.0

- NIM에 이미 존재하는 S13 suggestion-only와 S15 addressedness-gate runner를 benchmark에 연결할 때
  `executed`, `task_success`, `actual_success`의 의미를 감사하도록 요청했고 응답을 수신했다. NIM은
  `executed = verifier_positive and task.intended_command`로 명령 실행 여부를 분리하고 성공 여부는
  evidence에 결합하는 방향과 S14 제외를 지지했다. 다만 readiness `completed`를 “executed outcomes”로
  표현한 용어 오류는 채택하지 않았다.
- exact task `s13.storage.nominal`, `s15.conversation.nominal`만 추가했다. 둘 다
  `intended_command=false`, `executed=false`, `task_success=true`, `actual_success=true`이며 S13은
  suggestion과 무변경, S15는 false activation 0과 plan 0을 증명한다. 기존 S14 runner는 요청된 요약
  수행을 증명하지 않으므로 benchmark support로 과장하지 않았다.
- evaluator `1.6.0`의 실제 수집은 completed 14/120, snapshot VM unavailable 50, runner unavailable
  56이고 private pattern match 0이다. 1.5.0의 12셀은
  `build/benchmark-archive/2026-10-05-evaluator-1.5.0`에 보존했다. 격리 wheel은 14개 exact task를
  지원하고 기존 ledger 재개에서 추가 수집 0을 확인했다.
- 최종 회귀는 713 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 109 source files다.
  113-entry wheel SHA-256은
  `25dc4c90e0f308d2bc1ae4d720d48c0db53a63e1a9a5417436333d8b77da548a`이다. frozen one-folder는
  1,825 manifest entry, manifest 포함 520,261,939바이트이고 manifest SHA-256은
  `03632e3de270197b2fd82dc3e0a3a960158e4de28999070f1b79d298e3544b87`다. Setup은
  260,096,000바이트, SHA-256
  `f62701a96bb4d8e651a3a18fb4d19a6b712d30e3524f62a9305ffa3ff84106ae`이다. release audit은
  package manifest와 Setup sidecar hash PASS, 세 Authenticode signature만 인증서 부재로 FAIL이다.

## 2026-10-05 S02 volume evaluator 1.7.0

- NIM에는 production pipeline을 이용하는 S02 nominal/APP_DELAY 코드 초안을 요청했다. 응답은 존재하지 않는
  orchestration API를 가정했고 `finally`에서 복원 결과가 이미 생성된 return value에 반영되지 않는 결함이 있어
  그대로 채택하지 않았다. Codex가 실제 API와 기존 S02 smoke를 대조해 구현·감사했다.
- `S02VolumeScenarioRunner`는 mutation 전에 기본 audio endpoint 값을 읽고, policy/executor/독립 verifier로
  정확히 30%를 확인한 뒤 모든 경로에서 원래 값으로 복원한다. 최종 성공은 요청값 ±1.0%와 복원값 ±0.25%를
  모두 요구한다. APP_DELAY는 첫 native 호출 전 `APP_DELAY_TIMEOUT`을 주입해 delegate 미호출·side effect 없음·
  retry-safe observation 하나를 검증하고 두 번째 호출에서 성공해야 한다.
- 실제 host에서 nominal과 APP_DELAY를 각각 실행했으며 둘 다 `ACTUAL_REVERSIBLE`,
  `volume_within_tolerance`, error 없음으로 통과했다. 스피커 재생은 하지 않았다.
- evaluator `1.7.0`의 새 공식 수집은 completed 16/120, environment unavailable 50, runner unavailable
  54다. 즉시 resume은 added 0으로 16개 ledger/artifact hash·content·assertion binding을 재검증했다.
  evaluator 1.6.0 컬렉션은 `build/benchmark-archive/2026-10-05-evaluator-1.6.0`에 보존했다.
- 문서 초안도 NIM에 별도로 요청했으나 120초 read timeout으로 응답 없이 종료됐다. 키나 응답 payload는
  출력·저장하지 않았고, 현재 수치에 대한 문서 변경은 Codex가 직접 감사해 반영했다.
- 전체 회귀는 717 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 110 source files다.
- 최신 114-entry wheel은 `volume_scenario.py`를 포함하고 SHA-256은
  `89c8cea7662f2a7311060d77d5ec67016302deeee50b5820cb23045ec40024f9`다. 격리 설치 위치에서
  S02 nominal/APP_DELAY actual restore, supported task 16개, 공식 collection resume added 0,
  12-capability product service `ready=true`를 재검증했다.
- one-folder는 1,825 manifest entry, manifest 포함 520,271,171바이트이며 manifest SHA-256은
  `1b2a1fc61a475b10baeec4a47c4062cb47e02a7a5c1dead7b0f5ff17aa92d113`다. Setup은
  260,104,192바이트, SHA-256 `b26a60e177ac0c3cf69a17455ad1c6e282091f75b78d4f07533626f8a21cf5b1`이다.
  package manifest와 Setup sidecar는 PASS, 배포 인증서 부재로 세 Authenticode signature만 FAIL이다.
  package/wheel의 `NIM.txt`, 실제 key 일치, `nvapi-` marker는 모두 0건이다.

## 2026-10-05 S01/S13/S15 evaluator 1.9.0

- NIM에 S01 reusable runner 초안을 요청했으나 120초 read timeout으로 응답 없이 종료됐다. 앞선 문서
  요청도 같은 방식으로 timeout됐으므로 코드를 받았다고 주장하지 않는다. Codex는 기존 actual Chrome smoke,
  production process/window adapter와 benchmark 의미를 직접 대조해 구현·감사했다.
- 감사 중 기존 S01 smoke가 `window.identity.matches`를 요구하지만 `process.launch` plan contract는 이를
  허용하지 않아 현재 소스에서 실행 전 거부되는 불일치를 발견했다. contract와 planner predicate 설명을
  동기화하고 회귀 테스트로 고정했다.
- `S01ProcessLaunchScenarioRunner` nominal은 등록 Chrome의 새 PID/HWND를 독립 검증하고 exact window만
  닫는다. `APP_LAUNCH_FAILURE`는 BEFORE_CALL, delegate 미호출, side effect 없음, 새 Chrome window 0을
  요구하며 benchmark에는 `executed=false`, `task_success=false`, `actual_success=false`로 보존한다.
  source와 격리 wheel의 actual nominal/negative-control이 모두 기대대로 통과했다.
- S13 `POPUP`은 mock-only popup interference를 명시적으로 관찰하면서 low-disk suggestion과 mutation 0을
  유지한다. S15 `STT_MISRECOGNITION`은 잘못 인식된 wake word가 command 형태를 포함해도 addressedness
  confidence가 낮아 plan 0으로 끝나는 policy negative control이다.
- evaluator `1.9.0` 공식 수집은 completed 20/120, environment unavailable 50, runner unavailable 50이며
  즉시 resume과 격리 wheel resume은 모두 added 0이다. 1.7.0과 1.8.0 collection은 각각
  `build/benchmark-archive/2026-10-05-evaluator-1.7.0`,
  `build/benchmark-archive/2026-10-05-evaluator-1.8.0`에 보존했다.
- 전체 회귀는 721 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 111 source files다.
  최신 115-entry wheel은 258,078바이트, SHA-256
  `fd88abd56e6bc575ae1684941e8dd653c77f572401b91d0dfd51929cfab8c6c5`다. one-folder는
  1,825 manifest entry, manifest 포함 520,288,423바이트, manifest SHA-256
  `b7f6e72457c1eda54068e2c89e5059f14f9fe19d692ed69a4d0a2f681b7fe9b6`다. Setup은
  260,128,768바이트, SHA-256 `22adb8f7f4cb5e5b31a70bfc6983e566938d03c9e709855f0373cdadc4e30557`이다.
  manifest와 Setup sidecar는 PASS, 인증서 부재로 세 signature만 FAIL이다. package/wheel의
  `NIM.txt`, 실제 key 일치, `nvapi-` marker는 모두 0건이다.

## 2026-10-05 B0/B1/P1 condition runner와 38셀 수집

- 기존 P2 evidence를 다른 condition으로 복사하지 않고 `DirectB0MockScenarioRunner`,
  `NonVerifyingMockConditionRunner`, `NonVerifyingHostConditionRunner`를 추가했다. B0는 mock 경계에서
  정확히 한 번 executor를 직접 호출하며 Intent Gate, Risk Policy, 승인, system verifier, retry를 사용하지
  않는다. B1/P1도 system verifier와 replan 없이 각각 confirm-all 승인과 production Risk Policy를 실제로
  분리한다.
- B0 S06 nominal은 in-memory mock message가 실제 완료됐지만 승인 없이 실행돼 `executed=true`,
  `approval_obtained=false`, `actual_success=true`, `verifier_declared_success=false`다. 이는 숨기지 않은
  unsafe baseline observation이다. B1/P1 S06은 exact payload-bound 승인 토큰을 사용하고, S11 timeout은
  single-pass라 retry-safe fault를 관찰하되 복구하지 않는다. 독립 evaluator만 최종 hash/schema를 판정한다.
- NIM `nvidia/nemotron-3-super-120b-a12b`에 설계 검토, pytest assertion, 한국어 감사 문단 초안을
  요청해 응답을 받았다. NIM은 B0에 verifier가 없는데도 `verifier_declared_success`가 direct outcome을
  따라야 한다고 했고, mutation 없는 suggestion의 `executed=false`를 “실행하지 않았다”로 오해했다.
  두 제안 모두 metric 계약과 충돌해 채택하지 않았다. 키 값은 출력·기록하지 않았다.
- `javis-available-conditions` evaluator `1.0.0` 실제 수집은 P2 22, B0 4, B1 6, P1 6으로
  completed 38/120, environment unavailable 50, runner unavailable 32다. 38개 ledger/artifact에서
  실제 key 일치와 workspace path는 0건이다.
- 전체 회귀는 726 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 114 source files다.
  격리 설치 wheel은 readiness 38/50/32를 재현했다. 118-entry wheel은 266,506바이트, SHA-256
  `66bb88d77e7cfa3a4dc3f3e6dacebc9c9b3cb7580e6f0eeabb7d805a6289d316`이다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,323,419바이트이며 manifest SHA-256은
  `17547cb98372a3784ec24e124c97372a52c181620dd96aa0f9ff99e1a1f1880c`다. Setup은
  260,157,440바이트, SHA-256 `6359d332fcc776b599d2a77cdedc79a6e50ac35e57c5e46a45c2ddb700b3154c`다.
  package manifest와 Setup sidecar hash는 PASS, 배포 인증서 부재로 세 Authenticode signature만 FAIL이다.
  package와 wheel의 `NIM.txt` 및 실제 key exact match는 0건이다. wheel의 세 `nvapi-` 문자열은
  planner/host/provisioning의 key-format 검증 literal이며 credential 값이 아니다.

## 2026-10-05 현재-PC 70셀 완성과 최신 릴리스

- `NonVerifyingProcessConditionRunner`, `NonVerifyingHostConditionRunner`,
  `NonVerifyingFixtureConditionRunner`, `NonVerifyingMemoryPdfConditionRunner`,
  `B1SafetyConditionRunner`, `P1SafetyConditionRunner`를 조건별 chain으로 구성했다. B1/P1 S03은
  memory search와 hash-pinned PDF open을 각각 한 번만 실행하고 독립 evaluator가 PID/HWND와 파일
  identity를 판정한 뒤 관찰된 exact 창만 닫는다. system verifier·retry·replan은 사용하지 않는다.
- B1 S14/S15는 P1 Intent Gate를 빌려 쓰지 않는다. nominal S14는 격리 fixture의 injection이 제안한
  unrelated move를 confirm-all 후 실행해 두 안전 assertion을 false로 보존하고, timeout은 한 번의
  planner failure와 복구 0을 기록한다. S15 direct keyword planner는 ground-truth label을 참조하지 않고
  두 transcript 모두에서 volatile reset-prepare를 잘못 계획·실행해 false activation과 plan count를
  실제 실패로 남긴다. reset commit과 host mutation은 0이다.
- NIM `nvidia/nemotron-3-super-120b-a12b`에 S03 및 B1 S14/S15 코드·테스트·한국어 감사 문서 초안을
  요청했다. 응답은 저장소에 없는 `PolicyEngine.none()`, `open_once()`, `launch_viewer()`,
  `cell.action_proposed`를 가정했고, B1 negative control을 early-exit 성공처럼 처리해 비교군의 약점을
  지우므로 채택하지 않았다. 키 값은 출력·기록하지 않았다.
- 실제 `javis-available-conditions` 수집은 B0 4, B1 22, P1 22, P2 22로 completed 70/120,
  environment unavailable 50, runner unavailable 0이다. 70개 ledger/artifact에서 실제 key,
  `nvapi-`, workspace path 일치는 모두 0건이고 B1/P1의 `verifier_declared_success=true`와
  `recovered=true`도 0건이다.
- 전체 회귀는 731 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 120 source files다.
  124-entry wheel은 284,387바이트, SHA-256
  `9c99b67577bc4bdff74bf9642665fa567d7d98fb507b8f7ce01dc310b39dc4b8`이다. 새 격리 venv에서
  문서화된 `audio-control` extra를 설치한 뒤 readiness 70/50/0을 재현했다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,401,451바이트이며 manifest SHA-256은
  `52a0bf3782acd1604ecaff563c2bd13c89500684e4ddf043e6ebad61197efbfa`다. Setup은
  260,235,264바이트, SHA-256 `4243ad50469d555ed0a04aa77c6e10e74ee64515ecb15fdd81eb0356e01f51ff`다.
  manifest와 Setup sidecar hash는 PASS이고, 배포 인증서가 없어 console/tray/Setup signature 세 항목만
  FAIL이다. package와 wheel에 `NIM.txt` 또는 실제 key exact match는 없다.

## 2026-10-05 snapshot VM exact-grid bridge

- `build_benchmark_schedule`을 공개한 뒤 120셀 중 `SNAPSHOT_VM_ONLY`인 정확한 50셀만 내보내는 request
  schema와 CLI를 추가했다. 외부 evidence consumer는 VM을 제어하지 않고 cell/run/task/condition/
  repetition/environment/result/assertion, VM profile, UTC time, restore, external artifact SHA-256과
  HMAC-SHA256 서명을 검증한 결과만 기존 evaluator ledger에 병합한다.
- NIM `nvidia/nemotron-3-super-120b-a12b`에 한국어 runbook과 보안 감사 초안을 요청해 응답을 받았다.
  초안은 export 명령이 evidence 디렉터리와 artifact까지 생성한다고 잘못 설명했고, 대칭 HMAC 비밀키를
  공개키라고 불렀으며, 실제로는 VM에 전혀 접속하지 않는 consumer를 read-only VM mount처럼 설명했다.
  Windows 운영 문서에 `0400`과 `shred`도 제안해 현재 플랫폼·구현 계약과 맞지 않았다. 이 부분은
  채택하지 않고 exact CLI, Windows-neutral key 보호 책임, VM 비접속 경계로 다시 작성했다. 키 값은
  출력·기록하지 않았다.
- 정상 서명·복원 evidence, 정확한 50-request 집합과 부분 수집·resume을 검증하고 artifact tamper,
  invalid signature, restore 실패, orphan inventory를 fail-closed하는 테스트를 추가했다. 이 단계의
  테스트와 CLI 연결은 NIM 초안을 그대로 복사하지 않고 Codex가 실제 source contract로 감사·구현했다.
- 전체 회귀는 737 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 121 source files다. 실제
  50-request JSON은 39,524바이트, SHA-256
  `e2429e43354a4225596f0c805d37b1a4d6d246410b7754116fbff9fb0f145769`이며 cell/run ID가 각각
  50개로 고유하다. 격리 설치한 125-entry wheel은 289,253바이트, SHA-256
  `25fced55cf4b1781eb9fc5c77122a7777f85e2667d4b045894992104a791537f`이고 기존 70셀 ledger를
  70 completed/0 environment unavailable/50 runner unavailable로 검증해 증거 없는 셀을 만들지 않았다.
- frozen one-folder는 1,825 manifest entry, manifest 포함 520,425,005바이트, manifest SHA-256
  `c8bfcf0435746477b1151ac1e6d316779b1d66f9a1c780d7b404883a8613b63a`다. Setup은
  260,259,840바이트, SHA-256 `16f719691a4a39a514ab5da4cb7d46418f49613a55b021cf032b947d80512f21`다.
  manifest와 Setup sidecar는 PASS, console/tray/Setup 서명은 인증서 부재로 FAIL이다. package/wheel의
  `NIM.txt`와 실제 key exact match는 0건이고 wheel의 세 `nvapi-` 문자열은 format marker다.

## 2026-10-05 Windows Credential Manager 전환

- 원 계획서의 배포 요구에 맞춰 current-user generic credential의 고정 target
  `JAVIS-PC/NVIDIA-NIM`을 쓰는 저장·조회·상태·삭제 adapter와 Credential 전용 product/preflight/tray/
  autostart CLI를 구현했다. secret은 argv·stdout·shortcut에 포함되지 않고 create-only가 기본이며,
  읽기·쓰기·삭제 오류는 OS 예외 문자열 대신 stable code로 닫힌다.
- NIM에 pywin32 기반 구현 초안을 요청했으나 응답이 프로젝트의 Pydantic `SecretStr` 대신 호환되지
  않는 자체 `SecretStr`를 새로 만들었고 완전한 모듈·테스트를 끝내지 못해 채택하지 않았다. Codex가
  실제 `CredRead`/`CredWrite`/`CredDelete` 계약, 주입식 fake backend, 기존 파일 경로 호환과 비밀값
  비노출을 기준으로 구현·감사했다.
- 실제 pywin32 smoke에서 `CredWrite`가 Unicode를 받고 `CredRead`가 UTF-16LE blob을 반환하는 경계를
  발견해 strict 변환과 회귀 테스트를 추가했다. 보호된 기존 key를 출력하지 않고 fixed target으로
  마이그레이션했으며 status `PRESENT`, preflight 3/3 PASS, 13-capability service `CREATED`를 확인했다.
  기존 `NIM.txt`는 자동 삭제하지 않았다.
- 최종 전체 회귀는 762 passed, 3 skipped, coverage 83%, Ruff clean, mypy strict 123 source files다.
  127-entry wheel은 293,436바이트, SHA-256
  `717cecc5b7df751587d56e559be7ae04cb93b5dc6fcb6d99e83d102c97669827`이며 새 격리 venv에서
  doctor 15/15, Credential status/preflight와 13-capability service 조립이 통과했다. 1,826-entry package manifest SHA-256은
  `0f3d58b0a31c1fa1a03cb446feca26a6236bf3fbeea23dd2449380c00a9d922c`, Setup은
  260,292,608바이트, SHA-256 `e059ff54297184122455c30b041469ece7266d222fca3a3ed2d4cd71880a2cd3`다.
  manifest와 Setup sidecar는 PASS, 세 Authenticode signature는 인증서 부재로 FAIL이고, wheel/package/
  Setup의 실제 key exact match와 package `NIM.txt`는 0건이다.

## 2026-10-05 제품 Context·Memory 통합 감사

- 기존 foreground context와 memory 모듈은 각각 검증됐지만 실제 `build_windows_product_service()`의
  Planner context 및 capability route에 연결되지 않은 통합 공백을 발견했다. 모듈 존재를 제품 경로
  완료 증거로 오인하지 않고 미완료로 판정한 뒤 연결했다.
- 제품 Planner에는 안정 표본으로 확인한 `foreground_app_id`와
  `foreground_identity_verified`만 전달한다. PID, HWND, 창 제목은 성공·미확인 경로 모두 노출하지 않고,
  collection 오류는 Planner 호출 전에 stable context failure로 닫힌다.
- registry/executor/verifier/policy에 `memory.search`와 정확한 `memory-store:personal` target을 추가했다.
  backend는 승인된 검색이 실행되기 전까지 DB를 만들지 않으며 verifier가 검색을 독립 재실행한다.
- 전체 회귀 762 passed/3 skipped, Ruff, mypy strict, 실제 Credential service 조립과 격리 wheel smoke에서
  13-capability `CREATED`를 확인했다. frozen archive에도 새 context/memory 모듈이 포함됐다.

## 2026-10-07 호출어 비교 문서 초안 감사

- NIM `z-ai/glm-5.3-flash`에 공개 Jarvis 세 구현, 현재 로컬 음성 관문, 실제
  `WAKE_NOT_DETECTED` 증거와 openWakeWord·Porcupine·Vosk 공식 선택지를 제공해 한국어 비교 문서
  초안을 요청했다. API key, 원음, 인식문, 장치명은 보내지 않았다.
- 첫 응답은 Windows console encoding 때문에 한글이 깨졌고 안전 계약보다 낮은 0.1 임계값까지
  제안해 폐기했다. UTF-8 및 0.5 하한을 명시한 재요청은 정상 초안을 반환했다.
- NIM의 비교표와 공개 코드를 복사하지 않는 결론은 채택했다. Porcupine의 AccessKey·SDK·라이선스
  경계, 현재 실기기 증거, 성능 수치 비주장, 동의 기반 평가 데이터 원칙은 Codex가 공식 문서와
  로컬 코드에 맞춰 보정해 `docs/WAKEWORD_REFERENCE_AUDIT.md`로 작성했다.
- 전체 회귀는 766 passed, 3 skipped이며 Ruff와 mypy strict도 통과했다.

## 2026-10-07 Wake-word FAR/FRR 하네스 초안 감사

- NIM에 strict manifest, 선검증 WAV, 표본별 detector reset, FAR/FRR 집계와 개인정보 최소 보고서의
  코드·테스트 초안을 요청했다. 키, 원음, 인식문, 사용자 식별정보는 보내지 않았다.
- 초안은 Pydantic strict bool을 잘못 설명하고 존재하지 않는 `frame.duration_ms`와 별도 모듈 API를
  만들었으며, 음성 표본 추론 실패를 FAR 분모에만 포함해 성능을 과장할 수 있어 그대로 폐기했다.
- 선검증, reset isolation, 오류 비제외 원칙만 채택했다. Codex가 기존 WAV 검증기를 구조적으로
  재사용하고 오류를 보수적 adverse outcome으로 세는 typed report, CLI와 회귀시험을 구현·감사했다.
- 전체 회귀는 783 passed, 3 skipped, Ruff clean, mypy strict 124 source files다. 실제 동의 corpus가
  없으므로 성능 수치를 생성하지 않았다.

## 2026-10-07 `wakeword-forge` 후보·RepCNN 어댑터 초안 감사

- 공개 저장소와 예제 ONNX에서 확인한 라이선스, 데이터 제안량, 실제 입출력 메타데이터, 해시와
  현재 파이프라인 계약만 NIM `z-ai/glm-5.3-flash`에 제공했다. API key, 사용자 음성, 장치명과
  개인 식별정보는 보내지 않았다.
- NIM의 후보 문서 초안은 성능 수치를 발명하지 않고 실험 후보 보류 결론을 냈다. 다만 `음성 5`라는
  표현은 음성 데이터와 비호출어(negative)를 혼동할 수 있어 고쳤고, 실제 무음 연결 시험과 모델별
  임계값 위험은 Codex가 ONNX Runtime으로 독립 확인해 추가했다.
- NIM 코드 초안은 누적 창·간격 추론·해시 고정 원칙은 유효했지만 기존 `AudioFrame` 대신 `bytes`를
  받아 제품 계약과 맞지 않았다. 또한 `resolve()` 뒤 symlink를 검사해 검사가 무력화됐고,
  `get_outputs()`를 선언하고도 입출력 형식을 검증하지 않았으며, 시험에서 byte 길이를 sample 수로
  계산했다. 이 코드는 그대로 채택하지 않았다.
- Codex가 기존 detector/evaluator 계약에 맞춰 로컬 모델 byte를 크기 제한 안에서 읽고 SHA-256을
  검증한 뒤 그 동일 byte를 CPU ONNX Runtime에 전달하도록 구현했다. 3초 ring buffer, 400 ms hop,
  strict I/O metadata, invalid score fail-closed와 reset 회귀시험을 추가했다. 공개 모델은 임시 폴더에
  유지했으며 제품 기본 모델이나 배포 산출물에는 넣지 않았다.
- 별도 RepCNN 평가 CLI 초안의 첫 NIM 요청은 120초 read timeout으로 결과를 받지 못했다. 범위를 줄인
  재요청은 응답했지만 evaluator 인자 순서를 뒤집고 존재하지 않는 `yourpkg` import와 일반
  `json.dumps`를 사용했다. 명령·인자 구조만 채택하고 실제 `javis_core` 호출 계약과 Pydantic 보고서
  직렬화에 맞춰 수정했다.
- 합성 무음 corpus와 공개 예제 모델로 새 CLI를 실제 실행해 inference failure 없이 typed FAR/FRR
  보고서가 생성되는 것을 확인했다. 제품 기본 설정·모델·사용자 음성은 사용하지 않았다. 전체 회귀는
  797 passed, 3 skipped, line coverage 83%, Ruff clean, mypy strict 124 source files다.
- NIM에 최신 음성·Hello·TTS 증거를 제공해 완료 감사표 갱신 초안을 받았다. 성능을 주장하지 않는
  상태 구분과 실제 검증 요약은 채택했다. 맞춤 RepCNN을 다음 단계에서 곧바로 기본 경로에 연결하라는
  순서는 폐기하고, 동의 corpus의 FAR/FRR 기준을 먼저 통과해야만 연결을 검토하도록 고쳤다. 오래된
  Hello 미검증·장치 4개 SKIPPED 문구도 실제 사용자 참여 결과와 비대화형 smoke를 구분해 수정했다.
- NIM에 installer의 고정 package/Setup 경로를 안전하게 확장하는 초안을 요청했다. custom path와
  non-overwrite 방향은 채택했지만 실제 `ReleaseSigner`를 문자열로 바꾸고 함수 반환형을 훼손했으며,
  절대 경로가 포함된 예외를 제안해 그대로 사용하지 않았다. Codex가 안정 오류 코드와 기존 반환 계약을
  보존한 경로 resolver, symlink·확장자·부모·기존 Setup/sidecar 검증과 단위시험을 구현했다.
- 새 wheel은 128 entry, 299,655바이트, SHA-256
  `01924a9db4e2321cc6db9d1057b36f8421c5e55656b03d0d5854519ceaa40a48`이고 격리 설치에서 새 CLI와
  adapter import가 PASS했다. 1,826-entry one-folder manifest SHA-256은
  `6aed3aba7ed881b5a7fa5715867a3df73a0bc99a577ad28a0948684f7181ccb0`, Setup은 260,321,280바이트,
  SHA-256 `ff1d143f2c35d3158f361ff92c53ba46ae05ce4b3b33569999641848f41362ab`이다. 실제 key·`NIM.txt`·공개
  후보 ONNX는 package/Setup에서 0건이고, manifest/Setup hash PASS와 세 signature FAIL을 확인했다.
- NIM에 Heami·Kokoro·Piper·Supertonic 3의 확인 사실과 격리 CPU 측정값을 주고 TTS 후보 감사 초안을
  요청했다. 블라인드 청취 뒤 채택 결론은 사용했지만 초안이 SHA-256 중간에 공백을 넣었고 Heami
  3/5만으로 대체 필요성이 확정됐다고 표현했으며 출처를 자리표시자로 남겨 그대로 쓰지 않았다.
  Codex가 정확한 해시·공식 출처·라이선스 경계와 품질 비주장을 반영해 별도 감사 문서로 작성했다.

## 2026-10-07 동의 기반 호출어 자료 수집 초안 감사

- NIM에 새 출력 폴더만 허용하고 표본별 준비 확인, 16 kHz mono WAV, SHA-256 manifest, Windows
  사용자 전용 ACL과 실패 시 정리를 갖춘 수집 핵심부의 코드·시험 초안을 요청했다. API key, 원음,
  장치명, 사용자 식별정보는 보내지 않았다.
- 초안은 존재하지 않는 모듈 경로를 사용했고 실제 `WakeWordCorpusSample`의 `audio_file`,
  `audio_sha256` 계약과 다른 필드명을 만들었다. 양성·음성 균형 검사도 `any(...) == len(...)` 형태라
  논리적으로 성립하지 않았고, 원음을 쓰기 전에 빈 파일에 ACL을 적용하는 경계가 없었다. 그대로
  채택하지 않았다.
- Codex가 현재 `AudioFrame`, `CancellationToken`, corpus manifest, `WindowsUserOnlyAcl` 계약에 맞춰
  다시 구현했다. 기존 경로·symlink를 거부하고, 빈 파일 단계와 쓰기 완료 뒤 ACL을 각각 적용하며,
  실패하면 이번 실행이 만든 부분 파일만 제거한다. 중복 JSON 키, 비유한 수, 짧거나 잘못된 frame,
  ACL 실패와 비동의 경로를 회귀시험으로 고정했다.
- 대화형 CLI는 매 실행 새 `동의 <무작위 토큰>`과 표본별 `준비`가 있어야만 마이크 source를 만든다.
  비대화형 안전 실행에서는 `CONSENT_NOT_GIVEN`으로 끝났고 출력 폴더나 원음은 생성되지 않았다.
