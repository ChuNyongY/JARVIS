# 한국어 음성 평가 계약

음성 평가 하네스는 로컬 corpus 파일만 읽으며 마이크를 열거나 네트워크를 사용하지 않는다.
실제 녹음은 참가자의 별도 동의와 보관 정책이 필요하다. 이 저장소에는 그 경계를 강제하는
대화형 수집 명령도 있지만, 평가 명령과는 분리되어 있다.

별도의 `probe-microphones` 명령은 장치를 열지 않고 PortAudio의 device metadata와
`check_input_settings`만 조회한다. 반환값은 index, 입력 channel 수, 기본 sample rate, 기본 장치
여부, 16 kHz mono int16 호환 여부뿐이다. 장치명과 host API 이름은 반환하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m javis_core.cli probe-microphones
```

## Manifest와 입력 검증

- manifest는 UTF-8 JSON이며 최대 1 MiB, 최대 1,000개 sample로 제한한다.
- 각 sample은 고유한 `sample_id`, corpus root 기준 상대 POSIX WAV 경로, WAV SHA-256,
  한국어 reference text와 그 UTF-8 SHA-256, 비어 있지 않은 `consent_record_id`를 가진다.
- 절대 경로, `..`, Windows drive/역슬래시, symlink component, root 이탈을 거부한다.
- WAV는 mono, 16 kHz, uncompressed signed 16-bit PCM이어야 한다. 기본 한도는 sample당 2 MiB,
  corpus 총 100 MiB, sample당 30초다.
- 모든 sample의 경로·크기·hash·format·duration을 먼저 검증한다. 하나라도 실패하면 STT를 한 번도
  호출하지 않는다.

## Metric 정의

reference와 hypothesis는 Unicode NFKC 후 모든 whitespace를 단일 space로 정규화한다. CER은
space를 제외한 Unicode code point sequence의 Levenshtein distance 합을 reference character
수 합으로 나눈 micro average다. WER은 공백 분리 token sequence의 edit distance 합을 reference
word 수 합으로 나눈 micro average다.

STT inference 예외는 해당 sample의 빈 hypothesis로 처리해 CER/WER에서 reference 전체 삭제로
벌점을 주고 `STT_INFERENCE_FAILED`를 기록한다. 따라서 실패 sample을 분모에서 제거해 성능을
부풀리지 않는다.

## 개인정보 경계

평가 보고서에는 audio bytes, 파일 경로, reference/hypothesis 원문, consent record ID를 넣지
않는다. sample ID, 정규화 transcript SHA-256, 길이, edit distance, latency, error code만 기록한다.
SHA-256은 짧은 발화를 사전 공격으로 복원하지 못하게 보장하지 않으므로 보고서도 민감 연구
데이터로 취급해야 한다. `consent_record_id`는 실제 신원 정보가 아닌 외부 동의 기록의 opaque
reference만 사용한다.

이 하네스의 테스트는 injected STT와 합성 무음 WAV를 사용한다. 실제 한국어 화자 corpus, 장치별
잡음, wake-word 정확도, faster-whisper 모델 품질은 아직 측정하지 않았다.

## Wake-word FAR/FRR 평가

### 동의 기반 로컬 자료 수집

`capture-wakeword-corpus`는 계획 파일을 먼저 엄격히 검사한 뒤, 대화형 터미널에서 매 실행 새로
표시되는 `동의 <무작위 토큰>`을 정확히 입력한 경우에만 진행한다. 안내에는 표본 수, 녹음 시간,
원음 WAV가 로컬에 계속 보관된다는 사실이 포함된다. 각 표본 직전에도 `준비`를 입력해야 마이크를
연다. 비대화형 입력, Enter, 틀린 문구는 녹음을 시작하지 않거나 전체 수집을 취소한다.

```powershell
python -m javis_core.cli capture-wakeword-corpus `
  .\evaluation\wakeword_capture_plan.example.json `
  C:\absolute\new-wake-corpus-directory
```

출력 폴더는 존재하지 않는 절대 경로여야 한다. 수집기는 16 kHz mono signed 16-bit PCM을 표본별
WAV로 기록하고, 파일이 비어 있을 때부터 현재 사용자 전용 Windows ACL을 적용한다. 완성 뒤 다시
ACL을 검증하고 SHA-256과 동의 기록 식별자를 `manifest.json`에 저장한다. 중간 취소·장치 오류·ACL
실패 때는 이번 실행이 만든 파일만 지운다. 원음, 경로, 동의 식별자는 표준 출력에 표시하지 않는다.

예제 계획은 기능 확인용으로 양성·음성 각 2개뿐이다. 논문 성능 주장에는 화자·거리·소음 조건을
미리 고정하고 더 큰 자료를 별도 연구 동의하에 수집해야 한다. 2026-10-07에는 비대화형 동의 거부
경로만 실행했으며, 이 명령으로 실제 마이크를 열거나 원음을 생성하지 않았다.

`evaluate-wakeword-corpus`는 마이크와 네트워크를 열지 않고 동의·SHA-256이 결합된 로컬 WAV만
읽는다. manifest는 최소 한 개의 목표 호출어 표본과 한 개의 비호출어 표본을 포함해야 한다.

```powershell
python -m javis_core.cli evaluate-wakeword-corpus `
  C:\absolute\wake-corpus\manifest.json `
  C:\absolute\wake-corpus `
  .\models\openwakeword\manifest.json `
  --threshold 0.5
```

모든 파일의 상대경로, symlink, 크기, SHA-256, mono 16 kHz signed 16-bit PCM 형식과 길이를 먼저
검사한다. 하나라도 실패하면 detector를 한 번도 호출하지 않는다. 각 표본 전에는 openWakeWord
prediction buffer를 `reset()`해 이전 음성이 다음 표본에 섞이지 않게 한다.

보고서는 원음·파일 경로·동의 ID를 제외하고 sample ID, 목표 포함 여부, 검출 여부, 최대 점수,
첫 검출 상한 시각과 confusion count만 기록한다. 추론 오류는 분모에서 제거하지 않는다. 보수적으로
목표 표본 오류는 FN, 비호출어 표본 오류는 FP로 계산하고 `WAKEWORD_INFERENCE_FAILED`를 함께 남긴다.
임계값은 0.5~0.99만 허용한다.

- FAR = FP / 전체 비호출어 표본 수
- FRR = FN / 전체 목표 호출어 표본 수

이 값은 제공된 corpus와 마이크·거리·소음 조건에만 해당한다. 실제 동의 corpus 없이 합성 단위시험
결과를 제품 정확도로 주장하지 않는다.

`RepCnnWakeWordDetector`는 `wakeword-forge` 계열 ONNX를 비교하기 위한 실험 어댑터다. 16 kHz
mono PCM의 최근 48,000표본(3초)을 보관하고 창이 찬 직후 한 번, 그 뒤 6,400표본(400 ms)마다
추론한다. 모델은 네트워크에서 받지 않으며 호출자가 고정한 SHA-256, 16 MiB 크기 상한,
`float32[batch,time] -> float32[batch]` 계약을 모두 통과해야 한다. 제품 기본 호출어 경로에는
연결하지 않았고 다음 별도 평가 명령으로만 후보를 격리 비교한다. SHA-256은 64자리 소문자로
직접 제공해야 하며 모델별 threshold는 평가 전에 고정한다.

```powershell
python -m javis_core.cli evaluate-repcnn-wakeword-corpus `
  C:\absolute\wake-corpus\manifest.json `
  C:\absolute\wake-corpus `
  C:\absolute\model\jarvis.onnx `
  <64자리-model-sha256> `
  --threshold 0.7
```

합성 무음 양성·비호출어 표본 각 하나로 이 명령의 파일 검증, 모델 무결성 검사, 추론과 보고서 생성을
끝까지 확인했다. 임계값 0.7에서 FAR 0.0, FRR 1.0이었지만 호출어가 없는 합성 양성 표본이므로 이
수치는 모델 품질이나 한국어 성능의 근거가 아니다.

## Wake-word model bundle

`models/openwakeword/manifest.json`은 공식 openWakeWord v0.5.1의 ONNX wake-word와 feature model
두 개의 상대 경로·SHA-256·prediction key를 고정한다. loader는 중복 key, 추가 field, 절대·상위
경로, symlink, root 이탈, 크기 초과, hash 불일치를 거부하고 URL이나 model name download를
지원하지 않는다. detector 생성 직전 세 hash를 다시 확인한다.

## STT model bundle

`models/faster-whisper-tiny/manifest.json`은 `Systran/faster-whisper-tiny`의 immutable commit과
`config.json`, `model.bin`, `tokenizer.json`, `vocabulary.txt`의 byte size·SHA-256을 고정한다.
loader는 정확한 filename과 네 파일만을 계약으로 검증하며 runtime에는 검증된 local directory와
`local_files_only=True`를 전달한다. 이 tiny multilingual 모델은 runtime integration용 기준선이며
졸업논문의 한국어 정확도 결론에는 실제 동의 corpus로 측정한 CER/WER가 필요하다.
