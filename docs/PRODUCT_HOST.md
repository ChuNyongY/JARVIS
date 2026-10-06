# Product host 사전점검 계약

`config/product_host.json`은 실제 capture/runtime 조립에 사용할 비밀정보 없는 설정이다. 앱 registry,
Windows Hello helper, wakeword/STT manifest의 project-root 상대경로와 NIM model ID, 음성·HUD 상한만
포함한다. API key, transcript, 사용자 경로, 승인 token은 이 파일에 넣을 수 없다.

loader는 절대경로의 project root와 그 아래 `config`에 있는 non-symlink JSON만 허용한다. 중복 key,
비유한 수, 추가 field, traversal, 역슬래시·drive path, 누락 artifact와 symlink component를 거부한다.
설정 검증은 모델을 메모리에 올리거나 마이크·앱·HUD를 열지 않는다.

운영 기본 경로는 현재 사용자의 Windows Credential Manager generic credential
`JAVIS-PC/NVIDIA-NIM`이다. key는 echo 없는 prompt로만 받고 CLI 인자, 설정, preflight report와 로그에
포함하지 않는다. 저장 전후에 bounded `nvapi-` 형식을 검사하며 create-only가 기본이다. 같은 target의
교체는 라이브러리의 명시적 `replace=True`에서만 가능하고, CLI는 덮어쓰기를 제공하지 않는다.
Credential Manager API 자체가 원자적 create-only를 제공하지 않으므로 read-before-write는 동시 실행
경쟁까지 막는다고 주장하지 않는다.

기존 절대경로 secret 파일 경로는 호환·복구용으로 유지한다. 파일은 non-symlink regular file, 최대
512 byte, 단일 UTF-8 key와 선택적 마지막 newline만 허용하며 Windows DACL이 현재 사용자와
LocalSystem으로 보호됐는지 먼저 확인한다.

```powershell
$root = (Get-Location).Path
$config = (Resolve-Path .\config\product_host.json).Path
python -m javis_core.cli provision-nim-credential
python -m javis_core.cli nim-credential-status
python -m javis_core.cli preflight-product-host-credential $root $config
```

preflight는 `config`, `secret.nim`, `doctor` 세 항목만 stable code로 보고한다. 셋 중 하나라도 실패하면
exit 1이며 capture를 시작해서는 안 된다. 현재 이 PC에서 세 항목 모두 `PASS`, `ready=true`, exit 0을
실측했다.

이 단계는 안전한 시작 경계를 완성한 것이며 실제 always-on product host를 시작하지 않는다. 다음
조립 단계는 preflight 성공 후에만 verified bundle로 wake/STT를 만들고, capture pump와 runtime을
별도 수명 주기로 실행하며, 명시적 중지 요청으로 둘을 함께 종료해야 한다. 모든 capability adapter가
아직 하나의 consumer host로 조립됐다고 주장하지 않는다.

`ProductHostLifecycle`은 이 수명 주기의 실행 경계를 제공한다. `CREATED → PREFLIGHTING → RUNNING`
순서를 강제하고 preflight 실패·예외 시 capture thread를 생성하지 않는다. 명시적 `stop()`은
capture-stop 다음 runtime-stop을 호출하고 최대 30초 이내에 non-daemon capture worker가 끝나야
`STOPPED`가 된다. callback 실패, capture 예외, thread 시작 실패와 join timeout은 내부 예외 문자열을
숨긴 stable failure reason과 `FAILED` 상태로 닫힌다. 실제 adapter 조립은 주입식이므로 fixture에서
마이크 없이 lifecycle을 검증할 수 있다.

`build_product_voice_stack()`은 preflight가 반환한 trusted manifest 경로를 다시 로드·hash 검증한 뒤
openWakeWord, faster-whisper, bounded VAD, `VoicePipeline`, `SoundDeviceFrameSource`를 조립한다. bundle
검증이 먼저 실패하면 어떤 runtime component도 생성하지 않는다. frame source 생성은 장치를 열지
않으며 실제 capture는 lifecycle이 `frames()`를 소비할 때만 시작된다. 이 PC의 실제 로컬 bundle로
`OpenWakeWordDetector`와 `FasterWhisperSpeechToText` 초기화를 포함한 구성 smoke가 성공했고
`microphone_opened=false`를 유지했다.

`build_product_language_stack()`은 보호된 `SecretStr`와 config의 고정 model ID로 별도 typed intent
classifier와 typed planner transport를 만들지만 생성 시 network를 호출하지 않는다. registry는
planner와 runtime policy가 같은 객체를 공유해 capability drift를 막는다.

`build_product_runtime_stack()`은 voice/language stack을 policy, approval coordinator와 일회성 HMAC
token service, 주입된 executor/verifier/audit, always-on runtime과 capture pump로 연결한다. 외부 권한을
가진 executor, approval presenter와 strong authenticator는 자동 추정하지 않고 호출자가 명시적으로
주입해야 한다. synthetic audio fixture에서 wake/VAD/STT → typed intent → typed plan → policy → executor
→ verifier → audit → 완료 이벤트의 전체 폐루프를 통과했다. R0 GREEN fixture에서는 승인 UI 호출이
0회였고 실제 앱·파일·네트워크 외부효과도 없었다.

`CapabilityVerifierRouter`는 실제 product host가 capability별 독립 verifier를 하나의 검증 경계로
조립할 때 사용한다. 정확히 일치하는 capability ID만 위임하며, 미등록 capability, verifier 예외 또는
잘못된 반환 타입은 사용자 인자나 내부 예외를 노출하지 않는 복구 불가 stable failure로 닫힌다.
route mapping은 생성 시 복사되고 외부에서 변경할 수 없다.

`build_product_capability_stack()`은 executor route와 verifier route가 비어 있지 않고 정확히 같은
capability 집합인지, 그리고 모든 ID가 기준 registry에 존재하는지를 시작 전에 검사한다. 반환 registry는
실제로 실행하고 독립 검증할 수 있는 capability만 포함하므로 NIM planner에 구현되지 않은 권한을
광고하지 않는다. `build_product_runtime_stack_from_capabilities()`는 language stack이 이 narrowed
registry를 공유하는지 다시 검사하고, 하나라도 다르면 capture/runtime 생성 전에 거부한다.

`build_windows_product_service()`는 성공한 preflight 뒤에만 현재 Windows host용 13개 capability,
GLM-5.3-Flash typed language stack, verified local voice stack, 로컬 승인 UI, Windows Hello strong
authenticator, hash-chained audit sink, policy target matcher, privacy-minimized foreground context,
runtime과 capture lifecycle을 조립한다.
조립 자체는 마이크·HUD·앱을 열거나 음량을 변경하지 않는다. `check-product-service`는 이 전체 조립만
검사하고, `run-product-host`만 capture worker와 HUD를 시작한다.

현재 host capability는 `system.info.read`, `file.list`, `process.launch`, `explorer.folder.open`, `settings.open`,
`audio.volume.read`, `audio.volume.set`, `storage.status.read`, `cleanup.suggest`, `file.move`,
`file.delete.trash`, `browser.navigate`, `memory.search`다. `memory.search`는 `memory-store:personal`만
허용하고 승인된 실행 시점까지 SQLite DB를 만들지 않는 lazy backend를 사용하며 verifier가 같은 질의를
독립 재실행한다. Planner context는 등록 앱을 안정 표본으로 검증한 뒤 `foreground_app_id`와
`foreground_identity_verified`만 내보내며 PID, HWND, 창 제목은 내보내지 않는다. executor/verifier route가 하나라도 없으면 해당 capability는 NIM registry에서
제외된다. 음성 gate의 최저 신뢰도는 정책 context의 세 신뢰도 상한으로 전달되며 host provider가 이를
높일 수 없다. target identity는 capability별 인자와 허용 root를 실행 전에 다시 결합한다.
