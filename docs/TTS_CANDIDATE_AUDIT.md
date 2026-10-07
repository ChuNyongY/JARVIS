# 한국어 로컬 TTS 후보 감사

## 결론

현재 Windows SAPI/OneCore에 설치된 한국어 음성은 `Microsoft Heami` 하나뿐이다. 실제 스피커
검증은 PASS했지만 사용자 평가는 3/5였고 번역기 같은 말투를 개선해 달라는 의견이 있었다. 즉,
출력 기능은 동작하지만 자연스러움 개선 목표는 남아 있다.

제품 기본값은 아직 바꾸지 않는다. Supertonic 3를 가장 유력한 실험 후보로 두되, 같은 문장의
무작위·익명화 청취 평가와 라이선스 검토를 먼저 통과해야 한다.

## 후보 비교

| 후보 | 확인 사실 | 판단 |
|---|---|---|
| Windows Heami | 현재 PC의 유일한 한국어 SAPI/OneCore 음성, 실제 재생 PASS, 사용자 평점 3/5 | 동작 기준선으로 유지 |
| Kokoro | 공식 음성 목록에 한국어가 없음 | 제외 |
| Piper `ko_KR-kss-medium` | 63,221,984-byte ONNX, 1화자, 22,050 Hz | 데이터셋이 CC BY-NC-SA 4.0이므로 기본 배포 후보에서 보류 |
| Supertonic 3 | 한국어 포함 31개 언어, 10화자, 로컬 ONNX, 약 99M parameter | 블라인드 청취와 라이선스 검토용 우선 후보 |

Supertonic 3 원 모델 페이지는 model license를 OpenRAIL-M으로 명시한다. 반면 sherpa-onnx 변환
archive 안의 `LICENSE`는 MIT만 담고 있다. 변환 실행 코드와 원 모델 가중치의 라이선스가 다른 것으로
해석되므로, 원 모델 OpenRAIL-M 고지와 사용 제한을 보존하지 않은 채 배포하지 않는다.

## 이 PC의 격리 실행 결과

공식 sherpa-onnx release의
`sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2`를 제품 밖 `build` 실험 폴더에서만
사용했다.

- archive: 128,774,318 bytes
- SHA-256: `82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427`
- archive 검사: 10 entries, symlink/hardlink/path traversal 0
- runtime: sherpa-onnx 1.13.8, CPU 4 threads
- 초기화: 0.489초
- 고정 문장 10화자 출력: 44.1 kHz signed 16-bit PCM WAV
- 출력 길이: 2.835~3.408초
- 생성 시간: 0.334~0.408초
- RTF(생성 시간 / 음성 길이): 0.101~0.123
- Heami 기준 WAV: 22.05 kHz, 3.479초

이 수치는 한 PC에서 한 문장을 한 번씩 생성한 연결 시험이다. 평균 지연, 장시간 안정성, 명료도,
자연스러움 또는 사용자 선호를 증명하지 않는다. 자동 재생, 제품 통합, 배포는 수행하지 않았다.

Heami 1개와 Supertonic 10개를 무작위 A~K 파일로 복사한 청취 세트도 제품 밖
`build/supertonic-smoke-20261007/blind`에 만들었다. 공개 listening manifest SHA-256은
`ad16ca8c0d799ccafda2ce16d9e8a564f2b5073e32401921fb4715882fb40845`이다. 모델 대응표는 평가가
끝날 때까지 열지 않는다.

## 채택 실험

1. Heami 한 개와 Supertonic 10화자를 모델명이 보이지 않는 A~K 파일로 섞는다.
2. 모든 파일은 같은 고정 문장과 기본 속도로 생성하고 SHA-256으로 고정한다.
3. 사용자는 각 표본의 자연스러움·명료도를 1~5점으로 평가하고 가장 선호하는 표본을 고른다.
4. 최소한 Heami보다 선호도가 높고 자연스러움·명료도가 모두 4점 이상인 후보만 통합 대상으로 삼는다.
5. 라이선스 고지, 모델 파일 manifest, 로컬-only loader, 취소·timeout과 WAV 독립 검증을 설계한 뒤
   기존 SAPI를 fallback으로 유지한다.
6. 통합 후 같은 사용자 검증 절차로 실제 speaker 출력과 STOP을 다시 측정한다.

## 출처

- [Piper 한국어 모델 추가 commit과 모델 카드](https://huggingface.co/rhasspy/piper-voices/commit/f426ef352d13e24892d8b79e58659635bd6bfef7)
- [Kokoro 공식 음성 목록](https://github.com/OpenTTSGroup/kokoro-open-tts/blob/main/README.md)
- [Supertonic 3 공식 모델 카드](https://huggingface.co/Supertone/supertonic-3)
- [Supertonic 3 model license](https://huggingface.co/Supertone/supertonic-3/blob/main/LICENSE)
- [sherpa-onnx Supertonic 3 한국어 실행 문서](https://k2-fsa.github.io/sherpa/onnx/tts/all/Korean/supertonic-3-ko.html)
