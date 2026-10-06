# 저장공간 상태 및 정리 제안 경계

`storage.status.read`와 `cleanup.suggest`는 등록된 고정 root의 디스크 사용량만 읽는 R0 Native
capability다. 파일을 열거하거나 삭제·이동하지 않으며, 결과에도 실제 경로와 정확한 바이트 수를
포함하지 않는다.

## 공개 결과

- `root_id`: 설정에 등록된 불투명 식별자
- `free_percent_bucket`: 여유 공간을 구간화한 값
- `low_disk`: 임계값 미만 여부
- `suggestion_code`: `LOW_DISK_REVIEW` 또는 `NO_ACTION` (`cleanup.suggest`만 해당)
- `mutation_count`: 항상 `0`

executor는 절대 경로이며 symlink가 아닌 등록 root만 허용한다. verifier는 동일 root의 사용량을
독립적으로 다시 읽고 bucket과 low-disk 판정을 확인하며, mutation count가 0이 아니면 실패한다.
이 capability는 정리 후보 파일을 찾거나 자동 삭제하는 기능이 아니다.

## S13 검증 범위

`run-mock-scenarios`의 S13은 여유 공간 4%인 고정 backend를 사용해 `LOW_DISK_REVIEW` 제안이
생성되고 변경이 전혀 일어나지 않는지 확인한다. 2026-09-29 현재 S06과 함께 2/2 PASS이며
`external_effect_count=0`이다.

```powershell
python -m javis_core.cli run-mock-scenarios evaluation/integrated_scenarios.json
```

실제 파일 정리, 휴지통 이동, 삭제 복구 검증은 이 경계에 포함되지 않는다.
