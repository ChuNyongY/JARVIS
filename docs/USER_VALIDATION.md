# 사용자 참여 검증

`run-user-validation`은 자동화할 수 없는 실제 마이크·스피커·Windows Hello·STOP 검증을 한 단계씩
수행하는 대화형 CLI다. 명령 실행만으로는 어떤 장치도 사용하지 않는다. 각 단계 직전에 새로 표시된
`동의 <무작위 토큰>` 문구를 정확히 입력해야 해당 callback 하나만 실행된다. Enter, EOF, 잘못된 문구,
비대화형 입력은 모두 `SKIPPED / CONSENT_NOT_GIVEN`으로 처리된다.

```powershell
python -m javis_core.cli run-user-validation `
  C:\path\to\JAVIS `
  C:\path\to\JAVIS\config\product_host.json
```

단계는 다음 순서로 고정된다.

1. `MICROPHONE`: 동의 직후 공식 영어 호출어 모델에 맞춰 호출어는 영어식으로
   `Hey Jarvis, 시스템 정보 알려줘`라고 끊지 않고 말한다. 최대 10초 동안 로컬
   wake/VAD/STT를 실행한다. 인식 문장은 화면에 일시적으로만
   보여주며 사용자가 `예`로 정확성을 확인해야 PASS다.
2. `TTS_SPEAKER`: 고정 한국어 한 문장만 로컬 SAPI로 재생한다. 1~5점 평가를 받고 3점 이상을
   PASS로 기록한다.
3. `WINDOWS_HELLO`: hash-pinned helper가 OS-owned Windows Hello 창을 호출한다. helper의
   `VERIFIED` 결과만 PASS다. PIN이나 생체정보는 JAVIS가 받지 않는다.
4. `STOP_LATENCY`: 동의 직후 호출어는 영어식으로 `Hey Jarvis, 중지`라고 끊지 않고 말한다.
   최대 10초 음성 capture에서
   wake word 후 exact 로컬 STOP phrase를 검사한다. latency는
   microphone capture 시작부터 local detector 결정까지의 보수적인 상한이며 acoustic onset
   latency로 과장하지 않는다.

결과는 `state/user-validation.jsonl`에 append-only JSON으로 저장하고 현재 사용자와 LocalSystem만
허용하는 protected DACL을 적용한다. raw PCM, transcript, 장치명, Hello credential은 저장하지 않는다.
저장 필드는 stable code, PASS/FAIL/SKIPPED/ERROR, consent UUID, UTC 시각, 선택적 TTS rating과
STOP latency뿐이다. 네 단계가 모두 실제 PASS일 때만 report의 `complete`가 true다.

2026-10-03 비대화형 안전 smoke는 네 단계 모두 `SKIPPED`, callback 0회, exit 1을 반환했고 저장
JSONL에서 `transcript`와 `audio` 문자열이 모두 없음을 확인했다. 실제 장치 결과는 사용자가 각 단계에
직접 동의하기 전까지 완료로 주장하지 않는다.
