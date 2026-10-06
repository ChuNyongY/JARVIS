# Typed Intent Classifier

`TypedIntentClassifier`는 STT transcript를 실행 명령으로 바꾸는 구성요소가 아니다. NIM에는
의도 class, 세 신뢰도, 인용·부정·가정 발화 여부의 strict JSON 제안만 요청한다. 실제 라우팅과
실행 허용 여부는 각각 `IntentGate`와 capability policy가 결정한다.

다음 입력은 외부 모델보다 먼저 로컬에서 처리한다.

- 정확히 일치하는 한국어 STOP 문구는 `LocalStopDetector`가 `CANCEL` evidence로 만든다.
- `UNTRUSTED_CONTENT`는 원문을 모델로 보내지 않고 redacted `NON_TARGET` evidence로 만든다.
- wake confidence, explicit wake 여부와 source는 모델 출력에서 받지 않고 로컬 값을 결합한다.

모델 응답은 UTF-8 16 KiB 이하의 단일 JSON object여야 한다. duplicate key, markdown, NaN,
extra/missing field, 정수가 된 confidence, 숫자로 위장한 boolean, 모델이 만든 `CANCEL`을 모두
거부한다. transcript는 2,000자로 제한하며 오류에는 transcript나 transport 예외 원문을 싣지
않는다. 이 계약은 모델 분류의 정확도를 보증하지 않으므로 실제 corpus calibration과 오분류
평가는 별도로 수행해야 한다.

분류 결과가 `PLAN`인 경우에도 Planner는 원래 사용자 goal을 최종 Plan에 다시 결합한다. 모델이
goal 표시 문자열을 바꾸더라도 승인·감사에 쓰이는 goal은 변경되지 않는다. trusted context는
JSON 값만 허용하며 임의 Python 객체를 문자열로 바꾸지 않는다.
