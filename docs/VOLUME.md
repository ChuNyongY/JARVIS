# Windows master-volume 조회·제어 adapter

`audio.volume.read`는 기본 multimedia render endpoint의 master volume을 읽고,
`audio.volume.set`은 같은 값을 0~100 정수 percent로 설정한다. 위험 하한은 각각 R0와 R1이며
target identity는 `default-audio-render-endpoint`로 고정된다. 조회는 인자를 받지 않고
`volume.observed`, 설정은 `volume.matches` postcondition만 허용한다. 다른 장치 선택, mute,
per-application session과 임의 COM 호출은 제공하지 않는다.

실제 Windows backend는 optional `pycaw`의 `AudioUtilities.GetSpeakers().volume_percent`만 lazy
사용한다. 장치명이나 COM 예외 원문을 결과와 audit에 포함하지 않는다. 설치 방법은 다음과 같다.

```powershell
python -m pip install -e ".[audio-control]"
```

executor는 bounded lock 안에서 조회하거나 사전 read → set → 사후 read를 수행하고 기본 ±1
percentage point 안에서 일치해야 성공한다. 입력 전 STOP과 사전 read 실패만 retry-safe다.
`set_percent` 호출을
시작한 뒤의 예외, STOP, read 실패 또는 값 불일치는 상태가 불명확할 수 있으므로 자동 재시도를
금지한다. `VolumeMatchesVerifier`는 executor의 observed 값을 신뢰하지 않고 Core Audio 값을 다시
읽는다. 조회에서도 보고값과 독립 readback이 허용 오차 안에서 일치해야 하며, 불일치를
recoverable로 표시하지 않는다.

`tools/volume_smoke.py`는 현재 값을 읽고 가장 가까운 정수로 전체 policy/executor/verifier 경로를
통과한 다음 원래 값을 복원한다. 출력은 성공 여부, 안정적 error code와 복원 여부만 포함한다.
2026-09-29 실제 실행은 `success=true`, `original_state_restored=true`였고 장치 세부정보나 볼륨
수치는 출력하지 않았다. 이는 임의로 30%를 설정한 반복 사용자 실험이나 음향 품질 증거가 아니다.

`tools/s02_volume_smoke.py`는 S02의 정확한 30% 요청을 전체 policy/executor/verifier 경로로
실행하고 OS 값을 다시 읽은 뒤, `finally`에서 기존 값을 복원한다. 2026-09-29 실제 실행은
`success=true`, `requested_value_verified=true`, `original_state_restored=true`였다. 원래 볼륨과
장치 정보는 출력하지 않았다. 기본 safe-evaluation은 예기치 않은 음량 변경을 피하려고 이 actual
reversible smoke를 자동 재실행하지 않는다.
