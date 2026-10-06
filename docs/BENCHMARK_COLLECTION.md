# B0/B1/P1/P2 실제 관찰 수집 계약

`BenchmarkExperimentCollector`는 30개 고정 task를 B0, B1, P1, P2와 반복 번호의 exact grid로
전개한다. 1회 반복이면 120셀, 3회면 360셀이다. 셀 ID는 task ID, condition, 반복 번호,
선언 environment와 실제 execution environment의 canonical SHA-256이며 발화 원문은 ID·ledger·report에
포함하지 않는다.

## Environment 분리

결과 schema 1.2부터 evidence는 다음 두 값을 별도로 기록한다.

- `declared_environment`: task/scenario manifest가 요구하는 원래 환경
- `execution_environment`: 실제 trial을 실행한 환경

B1/P1/P2의 두 값은 같아야 한다. B0는 scenario의 `b0_isolation`에 따라 반드시 `MOCK_ONLY` 또는
`SNAPSHOT_VM_ONLY`여야 한다. 이전 schema 1.1은 B0를 mock/snapshot에서 실행하도록 요구하면서도
evidence environment를 원 task environment와 같게 요구하는 모순이 있어 실제 수집 전에 폐기했다.

## 기록 조건

collector는 `(StudyCondition, ScenarioEnvironment)`에 정확히 등록된 injected runner만 호출한다.
runner가 반환한 `TrialResult`의 run ID, intended-command label, ground-truth risk가 schedule과 같고,
task manifest의 모든 verification assertion이 정확히 한 번씩 평가됐을 때만 observation을 기록한다.
assertion의 `false`는 baseline·fault·실패 trial의 정상적인 관찰값이므로 artifact schema 1.1의
`verification_results`에 보존한다. assertion ID가 누락·추가된 경우만 runner 계약 오류로 거부한다.

각 완료 셀은 다음 순서로 저장된다.

1. 경로·화면·transcript·예외 원문이 없는 typed evidence artifact를 새 파일로 atomic write한다.
   임시 파일에 먼저 사용자 전용 권한을 적용한 뒤 rename하고 목적지 권한을 다시 검증한다.
2. artifact SHA-256을 포함한 observation을 append-only JSONL ledger에 기록하고 `fsync`한다.
3. Windows에서는 artifact, ledger, lock 파일을 현재 사용자와 LocalSystem 전용 DACL로 강화한다.
4. resume 시 ledger 중복·잘림·unknown cell, evaluator ID/version, result label, assertion,
   environment drift와 artifact hash/content binding을 전부 다시 검사한다.

artifact가 변조되거나 ledger와 정확히 대응하지 않는 orphan artifact가 하나라도 존재하면 덮어쓰거나 자동 복구하지 않고
fail-closed한다. 완료되지 않은 grid는 `BenchmarkObservationSet`으로 export할 수 없다.

## NOT_RUN 처리

환경 또는 runner가 없거나 assertion 집합이 불완전한 셀은 observation으로 만들지 않는다.
정확히 평가된 assertion이 `false`인 셀은 실패 관찰로 기록한다.
readiness report에서 다음 stable status로만 남는다.

- `COMPLETED`
- `READY`
- `ENVIRONMENT_UNAVAILABLE`
- `RUNNER_UNAVAILABLE`

runner 예외 원문도 기록하지 않는다. unavailable 셀은 뒤쪽의 실행 가능한 셀 수집을 막지 않지만,
최종 export에는 여전히 exact grid 전체가 필요하다.

2026-10-05 현재 PC에서 P2 nominal S01/S02/S03/S04/S05/S06/S07/S11을 exact scenario runner로 실행해
actual reversible fixture 또는 안전한 host/mock 증거를 기록했다. task별 `supports` 계약은 같은 condition/environment에 등록됐다는
이유만으로 미지원 task를 READY로 표시하지 않는다. nominal 결과를 다른 condition이나 fault task로
대체하지 않았다. S01 `APP_LAUNCH_FAILURE`는 launch delegate 호출 전 실패와 새 창 0을 증명하고 실제
task success는 false로 보존한다. S02와 S11 `APP_DELAY`는 첫 호출에 retry-safe timeout을 주입하고 두 번째 실행과
verifier가 복구한 증거까지 기록했다. S02는 실제 기본 audio endpoint를 30%로 설정해 독립 readback한 뒤
실행 전 값으로 복원하며, 정상·지연 셀 모두 복원까지 성공해야 완료된다. S04 `FILE_EXISTS`, S05 `NETWORK_DISCONNECT`, S06
`TARGET_MISMATCH`, S07 `USER_INTERFERENCE`도 외부 assertion 결과와 함께 기록한다. S03
`FILE_MISSING`은 검증된 memory search 뒤 누락 PDF를 viewer 실행 전에 거부한다. S03 nominal은
hash-pinned fixture PDF, 등록 Edge의 새 PID/HWND, 독립 verifier와 exact cleanup을 요구한다. 여기에
mock 격리 B0 S06/S13 4셀과 B1/P1의 현재-PC 대상 44셀을 condition 전용 runner로 추가했다. S01은
실제 Chrome, S02는 복원되는 실제 볼륨, S03은 memory→hash-pinned PDF→Edge, S04/S05/S07은 disposable
fixture, S06/S13은 mock, S11은 read-only host, S14/S15는 조건별 안전 제어를 사용한다. B0는
승인·정책·시스템 verifier·retry 없이 한 번 직접 실행하고, B1/P1은 시스템 verifier와 replan 없이
각각 confirm-all과 Intent Gate+production Risk Policy 경로를 사용한다. B1 S14/S15는 P1 Intent Gate를
재사용하지 않으며 injection 추종·false activation을 실제 실패 observation으로 남긴다. 현재 120셀 중
completed 70, ready 0, snapshot VM 부재로 environment unavailable 50, runner unavailable 0이다.
이는 일부 실제 실행 증거이지 완전한 작업 성공률이나 P2 성능 결과가 아니다.

수집 중 Windows wall-clock 해상도로 매우 짧은 trial의 시작·완료가 같은 결함을 발견했다. 시계 역행은
계속 거부하고 동률만 monotonic elapsed time으로 최소 1µs 보정한다. `javis-available-conditions`
evaluator `1.0.0`의 70개 ledger hash/content/assertion binding과 동일 readiness를 재검증했다.
전체 회귀는 737 passed, 3 skipped, line coverage 83%, Ruff clean, mypy strict 121개 source file이다. 합성 passing runner는
테스트 계약 검증에만 사용했고 실제 성능 관찰로 저장하지 않았다.

재현 명령:

```powershell
python -m javis_core.cli collect-available-benchmark `
  evaluation/benchmark_tasks.json `
  evaluation/integrated_scenarios.json `
  (Get-Location).Path `
  C:\absolute\benchmark-collection
```

남은 snapshot VM 50셀은 `export-snapshot-benchmark-requests`가 만든 exact 요청을 외부 harness에서
실행한 뒤 `collect-snapshot-benchmark`로 같은 collection root에 병합한다. consumer는 VM을 제어하지
않고 signed cell/result/assertion binding, restore 성공과 외부 artifact SHA-256을 검증한다. 일부
evidence만 들어오면 해당 셀만 기록하고 나머지는 `RUNNER_UNAVAILABLE`로 남기므로 70셀을 120셀로
과장하지 않는다. 상세 운영 계약은 `docs/SNAPSHOT_VM_EVIDENCE.md`에 있다.
