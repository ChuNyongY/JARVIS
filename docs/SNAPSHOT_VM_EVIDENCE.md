# Snapshot VM 증거 계약

JAVIS core는 hypervisor를 시작·중지·복원하거나 위험 작업을 직접 실행하지 않는다. 별도의 VM
harness가 snapshot 복원이 가능한 guest에서 시나리오를 실행하고, core는 그 harness가 내보낸
서명된 JSON 증거만 검증한다. 이 경계는 host에서 실수로 영구 삭제·종료·초기화 시험을 실행하는
것을 막기 위한 것이다.

## 입력 경계

- manifest는 `schema_version`, 고유 `scenario_id`, `vm_provider`, 불변 `base_snapshot_id`,
  `guest_machine_id`, `capability_id`, `expected_outcome`을 고정한다.
- evidence는 manifest binding 외에 고유 `run_id`, UTC 시작·종료 시각, 관찰 결과, snapshot 복원
  시도·성공 여부, artifact SHA-256, 서명 key ID와 서명을 포함한다.
- JSON은 UTF-8, 최대 64 KiB이며 중복 key, 추가 field, symlink, 비정규 파일, `NaN`/`Infinity`,
  timezone 없는 시각을 거부한다.
- 기본 freshness는 완료 후 1시간, 최대 실행 시간은 30분, 미래 clock skew는 2분이다.
- manifest 불일치, 예상 결과 불일치, 복원 미확인, stale/future 증거, 서명 verifier 예외는 모두
  fail-closed다.

## 서명 계약

`SnapshotSignatureVerifier` 구현은 배포자가 주입한다. core는 `signature` field만 제외한 evidence
전체를 UTF-8, key 정렬, 공백 없는 JSON으로 canonicalize해 verifier에 전달한다. 따라서 key ID,
artifact hash, restore 결과를 포함한 모든 판단 입력이 서명된다. core는 특정 알고리즘, 인증서
저장소 또는 key 배포 방식을 임의로 선택하지 않는다.

운영 verifier는 신뢰된 key ID allowlist, key 폐기, 알고리즘 고정, signature encoding의 strict
decode를 제공해야 한다. 검증 보고서는 서명 원문을 포함하지 않고 canonical payload SHA-256만
남긴다.

기본 운영 구현 `HmacSha256SnapshotVerifier`는 정확한 key ID 하나와 32~4096바이트 binary key를
받고 canonical payload의 HMAC-SHA256을 URL-safe base64로 검증한다. 비교에는 constant-time
comparison을 사용한다. key 파일은 절대 경로의 non-symlink regular file이어야 하며 저장소에는
포함하지 않는다. production에서는 guest harness와 verifier가 공유 key를 안전하게 배포·교체해야
한다.

## S08/S09/S10/S12 batch 검증

`SnapshotEvidenceBatchEvaluator`는 VM 전용 네 시나리오가 정확히 S08, S09, S10, S12 순서로
manifest에 존재하고, 각 scenario의 evidence가 정확히 하나씩 제공되는 경우에만 평가한다. 누락,
추가 scenario, 순서 변경, 중복 run ID는 실행 결과가 아니라 구성 오류로 거부한다. 출력에는
scenario ID, accepted, reason code와 signed-payload SHA-256만 남고 signature, 경로와 artifact
내용은 포함하지 않는다.

외부 harness가 `S08.json`, `S09.json`, `S10.json`, `S12.json`을 한 디렉터리에 생성한 뒤 다음처럼
검증한다. manifest 예시는 `evaluation/snapshot_scenarios.example.json`이며 placeholder를 실제
provider/snapshot/guest identity로 교체해야 한다.

```powershell
python -m javis_core.cli verify-snapshot-batch `
  C:\absolute\snapshot-scenarios.json `
  C:\absolute\evidence-directory `
  C:\absolute\hmac-key.bin `
  vm-harness-key-id
```

이 명령은 hypervisor를 시작하거나 guest action을 실행하지 않는다. 네 증거가 모두 fresh하고,
manifest/outcome/restore/signature 검증을 통과할 때만 exit code 0을 반환한다.

## 120셀 exact-grid 연결

위 네 시나리오 batch는 harness 신뢰 경계의 사전 점검이다. 논문용 30-task × B0/B1/P1/P2
격자에 결과를 넣으려면 셀 ID와 반복 번호까지 결합하는 별도 명령을 사용한다. 1회 반복에서
`SNAPSHOT_VM_ONLY`인 셀은 정확히 50개다.

먼저 새 절대경로 파일로 외부 harness 요청 집합을 내보낸다. 이 명령은 요청 JSON 하나만 만들며,
VM을 실행하거나 evidence·artifact를 대신 만들지 않는다.

```powershell
python -m javis_core.cli export-snapshot-benchmark-requests `
  evaluation/benchmark_tasks.json `
  evaluation/integrated_scenarios.json `
  C:\absolute\snapshot-benchmark-requests.json
```

외부 harness는 요청마다 snapshot에서 정확한 셀을 실행하고, 전용 evidence 디렉터리에
`<cell_id>.json`과 그 JSON이 지목하는 고유 `*.artifact`를 만든다. evidence는 cell/run/task/condition/
repetition, 선언·실행 environment, 전체 `TrialResult`, 모든 assertion의 boolean 결과, provider/base
snapshot/guest identity, UTC 시작·종료 시각, restore 시도·성공, artifact 파일명·SHA-256, key ID와
서명을 포함한다. 서명 대상은 `signature`만 제외한 canonical JSON 전체다.

HMAC key는 공개키가 아니라 harness와 검증기가 공유하는 비밀키다. 저장소·요청 파일·evidence·명령행에
키 바이트를 넣지 않고, 32~4096바이트 binary key를 별도 절대경로 non-symlink 파일로 전달한다.
운영자는 이 파일을 현재 검증 계정만 읽도록 보호하고 key ID별 회전·폐기를 관리해야 한다.

```powershell
python -m javis_core.cli collect-snapshot-benchmark `
  evaluation/benchmark_tasks.json `
  evaluation/integrated_scenarios.json `
  C:\absolute\benchmark-collection `
  C:\absolute\snapshot-evidence `
  C:\protected\snapshot-hmac-key.bin `
  vm-harness-key-id hyper-v baseline-sha256-01 javis-guest-01
```

수집기는 hypervisor나 guest에 접속하지 않는다. 디렉터리의 서명된 결과를 읽고 기존
`javis-available-conditions` evaluator 1.0.0 ledger에 검증된 셀만 추가한다. 증거가 일부만 있으면
그 셀만 기록하고 나머지는 `RUNNER_UNAVAILABLE`로 유지한다. 기존 70셀 ledger에 50개가 모두 합쳐져야
120/120이 되며, 누락 증거를 실행 결과로 만들지 않는다. `--repetitions N`은 요청 생성과 수집에서
동일해야 하고 기존 collection의 반복 수와 달라서는 안 된다.

각 JSON은 128 KiB 이하, 각 artifact는 기본 64 MiB 이하이어야 한다. filename/cell ID, VM profile,
cell/result/assertion binding, HMAC, artifact hash, 1시간 freshness, 30분 최대 실행시간, 2분 미래 skew,
restore 성공을 모두 통과해야 한다. 중복 cell/run ID, 중복 JSON key, non-finite number, symlink,
비정규 파일, 이름 규칙 위반, 참조되지 않은 파일이 하나라도 있으면 inventory 전체를 거부한다.

운영 감사에서는 다음을 확인한다.

1. 요청 파일과 harness가 동일한 task/scenario manifest 및 반복 수를 사용한다.
2. base snapshot ID는 mutable 별칭이 아니라 운영자가 고정한 불변 identity다.
3. guest와 검증 host에 배포한 HMAC key가 동일하고 허용된 key ID 하나에만 결합된다.
4. evidence 서명은 artifact 생성과 snapshot restore 성공 확인 뒤에 수행된다.
5. evidence 디렉터리는 셀 JSON과 정확히 참조된 artifact 외에는 비어 있다.
6. 수집 전 guest clock과 host UTC clock의 동기화를 확인한다.
7. `collected_this_run`, `completed`, `RUNNER_UNAVAILABLE` 수를 요청 개수와 대조한다.
8. 최종 120/120 후 artifact와 ledger를 분석기에 다시 읽혀 hash/content binding을 재검증한다.

## 외부 harness 요구사항

1. host와 구분되는 guest machine ID와 불변 base snapshot ID를 확인한다.
2. snapshot에서 부팅한 뒤 정확히 한 manifest 시나리오만 실행한다.
3. artifact를 보존하고 그 바이트의 SHA-256을 evidence에 기록한다.
4. snapshot 복원을 시도하고 성공을 확인한 뒤에만 `restore_succeeded=true`로 기록한다.
5. 모든 evidence field를 채운 후 canonical payload에 서명한다.
6. `(scenario_id, run_id)` 재사용을 외부 저장소에서 차단한다.

현재 core gate, batch evaluator와 exact-grid consumer는 evidence의 구조·binding·freshness·서명,
artifact byte identity와 복원 주장만 검증한다. VM 격리 자체, snapshot 불변성, guest clock, harness의
정직성, artifact 내용이 실제 UI·OS 결과를 충실히 나타내는지는 독립적으로 증명하지 않는다. 이것들은
신뢰된 외부 harness와 운영 절차의 책임이다.
