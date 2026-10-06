# 음성 명령 연결 계약

`VoiceCommandCoordinator`는 완료된 `VoiceCaptureResult`를 intent classifier와 결정론적
`IntentGate`에 연결한다. 명확한 `PLAN` route만 typed Planner로 전달하며, 여기서 반환되는 것은
아직 실행 권한이 없는 `Plan`이다. 실제 action은 반드시 Orchestrator의 action별 policy, 승인,
executor, postcondition 검증을 다시 거쳐야 한다.

capture abort, 빈 transcript, 분류 오류, 계획 오류는 안정적인 reason code로 실패하며 내부 예외나
transcript를 결과에 복사하지 않는다. 질문·대화·불확실 명령·승인 응답은 Planner로 보내지 않는다.

활성 작업 중 local STOP이 Gate에서 수락되면 주입된 `CancellationToken`을 즉시
`LOCAL_STOP`으로 set한다. 실제 Orchestrator와 coordinator에 반드시 같은 token 인스턴스를
주입해야 한다. token은 의도적으로 one-way이므로 새 작업 세션은 새 token과 coordinator를 만든다.
