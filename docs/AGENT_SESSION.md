# 단일 음성 작업 composition root

`AgentSession`은 실제 frame source에서 한 발화를 bounded capture하고, 다음 경계를 한 호출로 연결한다.

```text
FrameSource → VoicePipeline → VoiceCommandCoordinator
           → typed Plan → Orchestrator → TaskOutcome
           → fixed VoiceFeedbackPresenter
```

## 불변식

- 실행마다 새 one-way `CancellationToken` 하나를 만들고 coordinator와 orchestrator factory 양쪽에
  같은 token을 전달한다.
- frame 수는 1~1,500으로 제한하고 source iterator는 정상·오류·조기 capture 모두 명시적으로
  `close()`한다.
- capture, cleanup, context, coordination, orchestration 예외는 원문 없이 stable code로 바꾼다.
- Planner context provider 결과는 복사해 전달하며 실제 strict Planner가 JSON·크기 계약을 다시
  적용한다.
- 결과에는 transcript, audio, user text, model output, 경로 또는 내부 예외를 포함하지 않는다.
- task feedback은 검증 완료 뒤 고정 문장만 사용한다. feedback 실패는 이미 확정된 task outcome을
  실패로 바꾸지 않고 `feedback_delivered=false`로만 표시한다.
- source와 planner/executor/UI는 모두 주입식이므로 자동 시험은 마이크·스피커·OS 부작용 없이
  전체 연결을 검증한다.

## 현재 경계

이 composition root는 한 발화와 한 task를 끝까지 연결한다. 항상대기 루프와 실행 중 별도 음성
capture를 병렬로 유지하는 scheduler는 아직 포함하지 않는다. 실행 중 STOP token 전달 자체는
blocking executor를 별도 thread에서 실행한 통합시험으로 검증했지만, 실제 마이크에서 그 STOP을
동시에 수집하는 runtime과 latency 측정은 후속 항목이다.
