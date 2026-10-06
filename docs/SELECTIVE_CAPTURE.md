# Selective capture gate

Vision screenshot은 일반 context polling 수단이 아니다. `SelectiveCaptureGate`는 같은 action에서
UIA adapter가 입력을 수행하기 전에 명시적으로 해결 불가를 보고한 경우에만 세 번의 휘발성 window
capture를 허용한다.

허용되는 UIA 결과는 `UNSUPPORTED_CAPABILITY`, `ADAPTER_UNAVAILABLE`,
`UIA_GROUNDING_REQUIRED`이며 모두 `executor=uia`, 실패 상태, `retry_safe=true`여야 한다. action ID,
`process_id`, `target_identity=process:<pid>`가 일치하고 해당 PID가 여전히 승인된 launch session에
속해야 한다. 모호한 관찰, 입력 후 예외 또는 retry-safe가 아닌 실패는 Vision fallback 근거가 될 수
없다.

## One-shot capture 순서

1. `GROUNDING`: local grounder에 전달할 최초 window image
2. `PRE_INPUT`: 클릭 직전 동일 HWND·pixel hash 재확인
3. `VERIFICATION`: 입력 후 별도 predicate의 상태 변화 확인

각 purpose는 action마다 정확히 한 번만 소비할 수 있다. 기본 authorization은 30초 후 만료되고,
매 소비 시 process session을 다시 확인한다. 시간 역행·NaN/무한대 clock, 중복 authorization,
100개 기본 active action 상한 초과, purpose 재사용, PID 변경은 모두 거부한다. Vision adapter가
실패·예외·잘못된 action/executor 결과를 반환하면 broker가 남은 authorization을 닫고, 성공 경로는
verification predicate가 사용 여부와 관계없이 닫는다.

broker에 gate가 없거나 같은 action의 바로 앞 UIA 증거가 없으면 Vision adapter를 호출하지 않고
`VISION_FALLBACK_NOT_AUTHORIZED`로 종료한다. 따라서 registry에 Vision executor를 추가하는 것만으로
화면 접근 권한이 생기지 않는다.

이미지 bytes는 gate나 결과에 저장하지 않는다. gate에는 action UUID, PID, expiry와 남은 purpose만
존재한다. 이 계약은 selective capture 권한을 제한하지만 screenshot 내용의 의미적 안전성을
보증하지 않으므로 local-only grounding, trusted app profile, exact HWND 결합과 독립 postcondition이
추가로 필요하다.
