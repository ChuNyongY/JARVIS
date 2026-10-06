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

## `wakeword-forge` 실험 후보 감사

`wakeword-forge` v0.1.3은 Apache-2.0으로 공개되어 있고, WavLM 교사 모델을 이용해 약 217 KiB
RepCNN ONNX를 만드는 로컬 학습 흐름을 제공한다. 문서가 제시하는 시작 데이터는 목표 호출어 10개,
비호출어 5개, 배경 150개, 부분 발화 100개지만 공개된 교차 화자 한국어 `Jarvis` 성능 자료는 없다.
따라서 이 숫자는 제품 성능을 보장하는 최소 기준으로 사용하지 않는다.

공개 `okay-hermes` 예제의 `wakeword_v2.onnx`를 제품 밖 임시 폴더에서 검사했다. 파일 SHA-256은
`647fee171d7d98fa1672ec26f65f68a135471aea34bbdac4ede34693b89a68e1`이고 실제 ONNX Runtime
메타데이터는 `waveform: float32[batch,time]` 입력 하나와 `score: float32[batch]` 출력 하나다.
즉, 시간 축은 실제 그래프에서 가변 길이다. 그러나 예제의 학습·기본 사용 계약은 16 kHz 3초,
48,000표본을 권장한다. 예제 임계값 `0.697355628`도 `okay-hermes` 자료일 뿐 한국어 `Jarvis`의
채택 근거가 아니다.

현재 openWakeWord 어댑터는 80 ms `int16` 프레임과 별도 특징 모델을 사용하므로 이 RepCNN을 바로
교체할 수 없다. 별도의 실험 어댑터를 추가해 3초 창을 누적하고 400 ms마다 추론하게 했으며, 로컬
ONNX의 크기·SHA-256·입출력 계약을 먼저 검증한다. 공개 예제에 무음만 공급한 연결 시험은 38번째
80 ms 프레임에서 첫 추론, 이후 5프레임마다 추론했고 점수는 매번 `0.624035120`이었다. 두 번의
추론을 포함한 43프레임 처리 시간은 이 PC CPU에서 약 54.9 ms였다. 이는 연결·형식 확인일 뿐 정확도,
지연 상한 또는 한국어 성능 측정값이 아니다. 특히 일반 하한 0.5만 적용하면 이 예제의 무음 점수가
임계값을 넘으므로 모델별 임계값을 실제 FAR/FRR 자료로 따로 고정해야 한다.

결론은 **제품 기본 모델로 채택하지 않고 실험 후보로만 유지**하는 것이다. 사용자의 동의를 받은
한국어 목표·비호출어 corpus가 준비되면 현재 영어 모델과 같은 평가 하네스에서 화자·거리·소음별
FAR/FRR을 비교하고, 그 결과가 사전에 정한 기준을 만족할 때만 제품 경로 연결을 검토한다.

## 출처

- [GitHub jarvis-ai 주제](https://github.com/topics/jarvis-ai)
- [openWakeWord 공식 저장소](https://github.com/dscripka/openWakeWord)
- [공식 hey_jarvis 모델 설명](https://github.com/dscripka/openWakeWord/blob/main/docs/models/hey_jarvis.md)
- [Porcupine 모델 API와 지원 언어](https://picovoice.ai/docs/model-api/porcupine/)
- [Vosk 공식 사이트](https://alphacephei.com/vosk/)
- [`wakeword-forge` 공개 저장소](https://github.com/H-Ali13381/wakeword-forge)

