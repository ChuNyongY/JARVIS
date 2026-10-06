# 로컬 TTS 경계

JAVIS의 현재 TTS 구현은 Windows SAPI를 별도 Python worker process에서 호출한다. 텍스트는
네트워크나 NIM으로 보내지 않으며, worker에는 `speak` 또는 `render_wav`의 strict JSON 요청만
전달한다. 이 모듈은 응답 문장 생성기가 아니라 이미 확정된 짧은 문장을 로컬 음성으로 출력하는
제한된 sink다.

## 안전 계약

- 입력 텍스트는 1~500자이며 NUL을 거부한다.
- 속도는 -5~5, 음량은 0~100으로 제한한다.
- worker는 고정된 `python -I -m javis_core.tts_worker` 명령, `shell=False`, 최소 환경으로 실행한다.
- 요청·응답은 각각 8 KiB, 실행은 최대 30초로 제한하며 stderr와 내부 예외는 외부에 내보내지 않는다.
- JSON duplicate key, NaN/Infinity, extra field와 타입 coercion을 거부한다.
- SAPI `Speak`에는 plain-text 플래그를 사용해 XML/SSML 해석을 비활성화한다.
- `probe` 연산은 텍스트 없이 `SAPI.SpVoice` COM 객체 생성만 확인하며 소리나 파일을 만들지 않는다.
- WAV 출력은 주입된 절대 allowed root 아래의 새 `.wav` 파일만 허용하고 symlink와 기존 파일을
  거부한다.
- parent가 worker 응답을 신뢰하지 않고 파일 identity, 크기, SHA-256 및 WAV 구조를 다시 확인한다.
- 시작 전 이미 set된 전역 cancellation token은 SAPI 호출 전에 중단한다. 실행 중에는 10~250ms로
  제한된 poll 간격마다 같은 token을 확인하고, 취소 시 child를 terminate한 뒤 1초 내 끝나지 않으면
  kill한다. 취소·timeout·비정상 응답에 남은 부분 WAV는 제거한다.

## 실제 검증 범위

2026-09-22 현재 PC에서 네트워크와 스피커 출력을 사용하지 않고 `render_wav`를 실행했다.
최초 worker 경계에서 136,936바이트, 실행 중 취소 지원을 추가한 뒤 172,424바이트 WAV를 생성했고,
parent의 SHA-256 길이·파일 metadata·WAV frame 검사가 모두 통과했다. 별도 실제 worker 취소 smoke는
10ms 후 token을 set해 stable cancellation 오류를 반환했고 부분 WAV와 고아 worker를 남기지 않았다.
이 smoke는 SAPI IPC, STOP 및 파일 검증 경계를 확인하지만 한국어 발음 자연스러움,
실제 스피커 재생, 음량 안전성 또는 접근성 품질을 입증하지 않는다.

고정 성공 feedback인 `요청한 작업을 확인하고 완료했습니다.`도 별도로 렌더링했다. 결과는
154,344바이트, mono 16-bit, 22,050 Hz, 77,149 frame(3.499초) WAV였고 동일한 parent 검증을
통과했다. 이는 한국어 코드 경로와 비어 있지 않은 audio frame만 확인하며 사람이 들은 발음
품질 평가는 아니다.

`speak`는 실제 소리를 발생시키므로 자동 회귀검증에서는 호출하지 않는다. 사용자 참여형 검증은
짧은 고정 문장, 사전 안내, 즉시 STOP 수단을 갖춘 상태에서 별도로 수행해야 한다.

`VoiceFeedbackPresenter`는 plan 준비, 검증 완료, 안전 중단, 승인 필요, 취소, 실패의 여섯 상태만
고정 한국어 문장으로 바꾼다. `TaskOutcome.reason`, 사용자 발화, NIM 응답, 경로 또는 내부 오류는
읽지 않는다. 같은 shared cancellation token을 TTS sink에 전달하므로 STOP이 feedback worker도
종료한다. 자유 형식 대화 응답을 생성하거나 읽는 기능은 이 경계에 포함되지 않는다.

배포 전 `javis doctor`는 5초 제한 worker에서 이 `probe`를 실행한다. 실패는 내부 COM 예외나
경로를 내보내지 않고 `tts.sapi` / `SAPI_UNAVAILABLE`로만 보고한다.
