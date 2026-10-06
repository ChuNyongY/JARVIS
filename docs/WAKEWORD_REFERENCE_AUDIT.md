# 호출어 감지 방식 비교와 선택

## 결론

GitHub `jarvis-ai` 주제의 대표 구현을 그대로 복사하지 않는다. 조사한 세 프로젝트는 전용 호출어
모델보다 클라우드 음성 인식 결과에 `jarvis`라는 문자열이 들어 있는지 확인하는 방식에 가깝다.
JAVIS-PC는 현재의 로컬 호출어 관문을 유지하고, 한국어 맞춤 ONNX 모델을 별도 실험으로 평가한다.

## 확인한 구현

| 프로젝트·방식 | 호출어 판정 | 네트워크·개인정보 경계 | JAVIS-PC 적용 판단 |
|---|---|---|---|
| `Likhithsai2580/JARVIS` | 전체 발화를 `recognize_google`로 바꾼 뒤 `jarvis` 문자열 포함 여부 확인 | 호출어 판정 전에 발화가 Google STT로 전달됨 | 복사하지 않음 |
| `GauravSingh9356/J.A.R.V.I.S` | 매 발화를 `recognize_google(..., language="en-in")`로 처리 | 인터넷과 외부 STT에 의존 | 복사하지 않음 |
| `kishanrajput23/Jarvis-Desktop-Voice-Assistant` | 매 발화를 `recognize_google(..., language="en-in")`로 처리 | 인터넷과 외부 STT에 의존 | 복사하지 않음 |
| JAVIS-PC 현재 구조 | 16 kHz 로컬 PCM → 에너지 VAD → openWakeWord ONNX → 로컬 faster-whisper | 호출어와 STT가 로컬이며 원음·인식문을 보고서에 저장하지 않음 | 구조 유지, 모델 교체 실험 |

문자열 검사는 구현이 쉽지만 전용 호출어 검출이 아니다. 일반 대화도 먼저 STT해야 하므로 지연과
계산 비용이 늘고, 네트워크가 끊기면 동작하지 않으며, 단순 부분 문자열 때문에 오작동 경계도 약하다.

## 대안 비교

| 대안 | 장점 | 비용·제약 | 판단 |
|---|---|---|---|
| openWakeWord 맞춤 모델 | 기존 ONNX 어댑터·무네트워크 실행·manifest 무결성 검사를 재사용 가능 | 맞춤 학습과 실제 FAR/FRR 평가가 필요함 | 우선 실험 |
| Picovoice Porcupine | 한국어, Windows, 맞춤 문구를 공식 지원하며 장치 내 추론 가능 | 별도 AccessKey, SDK, 모델 생성 서비스 및 라이선스 검토 필요 | 비교군 후보 |
| Vosk 한국어 스트리밍 STT | 한국어를 오프라인으로 처리 가능 | 전용 KWS보다 호출 전 처리 범위와 계산량이 큼 | 주 호출어 대체재로는 보류 |

openWakeWord 공식 문서는 맞춤 모델에 최소 수천 개의 양성 예시와 대규모 음성·소음·음악 음성
데이터를 권장한다. 빠른 데모 모델의 존재만으로 실제 환경 성능을 주장하지 않는다.

## 현재 PC에서 확인한 사실

- 기본 입력 장치는 16 kHz mono 입력과 호환되고 실제 프레임과 음성 에너지가 들어왔다.
- 공식 영어 `hey_jarvis_v0.1` 모델을 임계값 0.5로 실행했다.
- 한국어식 발음과 영어식 `Hey Jarvis` 발음 모두 두 번의 동의 기반 시험에서
  `WAKE_NOT_DETECTED`로 끝났다.
- 따라서 현재 실패 지점은 마이크나 STT가 아니라 호출어 모델이다.
- 임계값을 0.5 아래로 낮춰 통과시키지 않는다. 이는 오검출 위험을 키우고 문제를 숨길 수 있다.

## 한국어 맞춤 모델 채택 실험

기존 모델과 후보 모델을 같은 녹음 조건에서 비교한다. 원음은 사용자가 명시적으로 동의한 평가
세션에서만 수집하고, 기본 운영 로그에는 저장하지 않는다.

1. 목표 호출어 양성 발화를 화자·거리·소음 조건별로 나눈다.
2. 비슷한 발음, 일반 대화, 음악, 무음으로 음성·비음성 음성 데이터를 구성한다.
3. 학습에 쓰지 않은 평가 집합에서 다음 값을 함께 기록한다.
   - FAR(False Acceptance Rate, 오검출률): 호출어가 없는데 켜진 비율
   - FRR(False Rejection Rate, 미검출률): 호출어가 있는데 놓친 비율
4. 임계값은 0.5 이상 범위에서만 비교하고, FAR와 FRR 어느 한쪽만 좋아진 결과를 채택하지 않는다.
5. 현재 영어 모델, 한국어 맞춤 openWakeWord ONNX, 필요할 때만 Porcupine 비교군을 동일 조건으로
   측정한다.
6. 목표 기준은 실제 측정 전에 고정한다. 현재는 측정값이 없으므로 개선 비율을 주장하지 않는다.

## 출처

- [GitHub jarvis-ai 주제](https://github.com/topics/jarvis-ai)
- [openWakeWord 공식 저장소](https://github.com/dscripka/openWakeWord)
- [공식 hey_jarvis 모델 설명](https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md)
- [Porcupine 모델 API와 지원 언어](https://picovoice.ai/docs/model-api/porcupine/)
- [Vosk 공식 사이트](https://alphacephei.com/vosk/)

