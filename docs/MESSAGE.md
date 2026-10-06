# 메시지 draft/send 경계

`message.draft`는 R1, `message.send`는 외부 효과가 있는 R3 capability다. 두 action 모두 bounded
recipient/body, `recipient-sha256:<digest>` target과 각각 `message.drafted` 또는 `message.sent`
postcondition을 요구한다. Orchestrator의 RED 승인 토큰은 recipient와 body가 포함된 전체 arguments
hash, action, target과 risk에 결합된다.

기본 제공 `InMemoryMessageSink`는 실제 메시지를 보내지 않는다. recipient와 body 원문을 저장하지
않고 각각의 SHA-256, kind와 무작위 receipt ID만 bounded memory에 유지한다. 실행 및 verifier
결과에도 원문은 없고 hash, delivery mode와 receipt만 있다. 외부 효과가 있다고 표시된 sink는
`allow_external=true`를 명시하지 않으면 구성 단계에서 거부되며, 현재 저장소에는 실제 서비스
backend가 없다.

executor는 호출 전 STOP만 retry-safe로 처리한다. `message.send` 호출이 시작된 뒤 발생한 예외,
잘못된 receipt와 STOP은 실제 외부 전송 여부가 불명확할 수 있으므로 자동 재시도를 금지한다.
`MessageReceiptVerifier`는 action에서 recipient/body hash를 다시 만들고 sink의 receipt record와
독립 비교한다.

`run-mock-scenarios`는 S06 하나를 실제 R3 policy, synthetic exact-action approval token, mock send,
receipt verifier로 실행한다. 현재 결과는 1/1 PASS, external effect 0이다. 이는 실제 사용자 승인,
실제 연락처 해석이나 실제 메시지 전송 성공을 증명하지 않는다.
