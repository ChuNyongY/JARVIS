# 상시 음성 런타임 계약

`AlwaysOnCapturePump`와 `AlwaysOnAgentRuntime`은 하나의 장기 실행 음성 수집 경로를 작업 실행과
분리한다. 캡처 펌프는 `SoundDeviceFrameSource` 같은 bounded frame source를 계속 소비하고,
`VoicePipeline`이 완성한 발화만 런타임에 전달한다. 런타임은 계획과 실행을 전용 daemon worker에서
수행하므로 계획·실행·고정 음성 피드백 중에도 캡처 루프가 다음 발화를 처리할 수 있다.

## 상태와 동시성

- `IDLE`: 새 발화를 받아 task별 cancellation token과 worker를 만든다.
- `PLANNING`: 전경 컨텍스트 수집, intent 분류, typed planning을 worker에서 수행한다.
- `EXECUTING`: 같은 token으로 Orchestrator와 고정 feedback을 수행한다.
- `CLOSED`: 새 발화를 거부하고 활성 token을 취소한다.

`PLANNING` 또는 `EXECUTING`에서는 `LocalStopDetector`의 exact-match 한국어 STOP만 동기적으로
수락한다. 이 경로는 컨텍스트 공급자, intent classifier, NIM Planner를 호출하지 않는다. 다른
발화는 `BUSY_IGNORED`로 반환한다. 느리거나 멈춘 컨텍스트 공급자도 STOP 전달을 막지 않으며,
취소 확인 뒤에는 classifier와 Planner를 호출하지 않는다.

캡처 수명 token과 task token은 별개다. `request_stop()`은 마이크 루프만 중단하며 실행 중인
task를 승인하거나 취소하지 않는다. task 중단은 음성 STOP 또는 `AlwaysOnAgentRuntime.close()`로
명시한다. 각 task가 끝나면 새 token을 만들기 때문에 이전 STOP이 다음 task로 전파되지 않는다.

## 종료와 결과 최소화

- 캡처 iterator와 pipeline은 정상 종료, frame 상한, 예외, 취소에서 모두 정리한다.
- 기본 frame 상한은 1,080,000이며 상한 도달 뒤 호출자가 새 실행 주기를 시작해야 한다.
- runtime 종료는 최대 30초의 bounded join을 사용하고 비협조 worker는
  `RUNTIME_STOP_TIMEOUT`으로 보고한다.
- 결과에는 frame/capture/event 개수와 stable code만 포함하며 transcript, audio, 장치명,
  컨텍스트 내용 또는 내부 예외를 저장하지 않는다.
- 완료 이벤트 queue는 최근 100개로 제한된다. 장기 운영 host는 주기적으로 `poll_event()`를
  소비해야 한다.

## 상태 HUD 경계

`status_snapshot()`은 이벤트 queue를 소비하지 않고 `IDLE/PLANNING/EXECUTING/CLOSED`, 고정 reason,
완료 action 수와 단조 증가 sequence만 복사해 반환한다. transcript, goal, 파일 경로, 창 제목,
task UUID는 모델에 존재하지 않는다. 따라서 UI polling이 실행 결과 소비자와 경쟁하지 않는다.

`RuntimeStatusPresenter`는 UI 소유 thread에서 snapshot을 polling하며 알려진 reason만 한국어 고정
문구로 표시한다. 알 수 없는 reason과 provider 예외는 내부 문자열을 노출하지 않고 일반 문구로
축약한다. `TkStatusView`는 tkinter를 생성할 수 없으면 명시적으로 실패하며 백그라운드 thread에서
widget을 호출하지 않는다. 창 닫기·숨기기는 HUD만 종료하고 agent를 암묵적으로 중단하지 않는다.
`중지` 버튼만 주입된 capture-stop과 runtime-stop callback을 이 순서로 호출한다.

이 최소 HUD와 별도로 `WindowsTrayController`가 notification-area 아이콘의 상태 보기·중지·종료만
제공한다. callback 예외는 고정 오류로 축약되고 `WM_DESTROY`는 아이콘 제거와 quit만 수행해 재귀
destroy를 피한다. 운영 기본인 `run-product-tray-credential`이 실제 capture pump와 runtime을
Credential Manager key로 조립하며, 기존 `run-product-tray` 파일 경로도 호환용으로 유지한다. autostart는 사용자
명령으로만 현재 사용자 `JAVIS.lnk`를 등록하고 정확히 소유한 shortcut만 제거한다.

## 검증 범위와 한계

자동 테스트는 단일 캡처 펌프가 blocking planning 중 두 번째 STOP 발화를 전달하는 경로,
느린 컨텍스트 수집을 우회하는 STOP, task별 token 재생성, worker 시작 실패 복구, bounded 종료,
capture 정리 및 transcript 비보존을 검증한다. 아직 실제 마이크를 열지 않았으며 실제 한국어
wake/STT 정확도, STOP end-to-end latency, 장시간 장치 변경·절전 복구는 사용자 동의가 필요한
실기기 검증 항목이다.
