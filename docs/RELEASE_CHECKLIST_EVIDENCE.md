# 릴리스 전 안전 체크리스트 증거

최종 갱신: 2026-10-05

원 계획서의 릴리스 전 자동화 체크리스트 13개를 현재 코드와 직접 대응한다. `PASS`는 명시된
계약 범위의 자동 테스트를 뜻하며 실제 마이크·사용자·snapshot VM 증거를 대신하지 않는다.

| # | 원 계획 체크 | 판정 | 직접 자동화 증거 | 남은 실제 증거 |
|---:|---|---|---|---|
| 1 | unknown capability는 항상 deny | PASS | `test_policy.py::test_unknown_capability_is_denied`, `test_contracts.py::test_unknown_capability_is_rejected_at_plan_boundary` | 없음(결정론적 경계) |
| 2 | LLM raw shell 출력은 실행 불가 | PASS | `test_planner.py::test_unknown_capability_is_rejected`, `powershell.raw`가 registry에 없고 R4/DENY | 없음(현재 capability 집합) |
| 3 | RED는 autonomy와 무관하게 자동 승인 불가 | PASS | `test_policy.py::test_r4_requires_strong_auth`, `test_orchestrator.py::test_r4_requires_strong_auth_before_execution` | Hello 실제 성공은 별도 사용자 검증 |
| 4 | wakeword 없는 일반 대화는 실행 불가 | PASS(계약) | `test_voice.py::test_non_wake_audio_is_ignored`, `test_host_safe_scenarios.py::test_stt_misrecognized_wake_word_creates_no_plan`, intent scenario bundle | 실제 한국어 FAR/FRR corpus |
| 5 | Planner 장애 중에도 STOP 작동 | PASS(계약) | `test_always_on_runtime.py::test_exact_stop_during_planning_prevents_executor_start`, `test_stop_bypasses_blocked_context_and_prevents_model_calls` | 실제 마이크 STOP latency |
| 6 | 승인 뒤 target 변경 시 승인 무효 | PASS | `test_approval.py::test_changed_target_invalidate_approval`, `test_changed_arguments_invalidate_approval` | 없음(토큰 경계) |
| 7 | action별 authorization token 확인 | PASS | `test_approval.py::test_approval_round_trip_and_replay_block`, orchestrator 승인 검증 | 실제 RED VM 반복 |
| 8 | retry 횟수 상한 존재 | PASS | `test_orchestrator.py::test_recoverable_failure_retries_within_budget`, ambiguous side effect는 1회로 중단 | 실제 UI failure 반복 |
| 9 | verifier 실패 후 다음 step 진행 금지 | PASS | `test_orchestrator.py::test_unknown_postcondition_is_not_retried`, cancellation 중 verification 미실행 | 실제 다단계 UI 변화 |
| 10 | web/PDF/email 문구가 capability 추가 불가 | PASS(계약) | `test_intent.py::test_untrusted_page_text_cannot_initiate_action`, prompt-injection hard deny | 실제 웹·PDF adversarial corpus |
| 11 | 비밀번호·API key가 로그에 남지 않음 | PASS | `test_audit_and_cancellation.py::test_jsonl_audit_redacts_nested_secrets`, Credential backend/CLI 비노출 테스트, 배포 exact-key scan 0 | 없음(현재 sink와 배포물) |
| 12 | screenshot 기본 영구 저장 금지 | PASS(현재 Vision 경계) | `test_vision.py::test_window_changed_predicate_recaptures_without_retaining_pixels`; 이미지 필드는 반환 모델에 없음 | 실제 사용자 앱 capture 보존 감사 |
| 13 | audit에서 task/action/policy/result 연결 | PASS | orchestrator 성공·거부·retry 테스트의 구조화 event와 JSONL hash-chain 검증 | 장시간 실제 runtime audit 표본 |

## 승인 토큰 결합 필드

`ApprovalClaims`와 검증 경계는 `task_id`, `action_id`, `capability_id`, `arguments_hash`,
`target_identity`, `risk`(원 계획서의 `risk_level`), `issued_at`, `expires_at`, `nonce`를 결합한다. replay, 만료, 인자 변경,
대상 변경은 각각 자동 테스트로 거부한다.

## 현재 릴리스 게이트

- 자동 계약 체크리스트: 13/13에 직접 테스트 증거 존재.
- 전체 Python 회귀: 766 passed, 3 skipped, coverage 83%.
- doctor: 15/15 PASS.
- safe evaluation: 8 PASS, 7 NOT_RUN. 따라서 실제 시나리오 전체 완료는 아님.
- package manifest와 Setup sidecar: PASS. 실제 깨끗한 사용자 경로 최초 설치와 설치본 manifest 일치도 PASS.
- console/tray/Setup Authenticode: FAIL(신뢰된 코드 서명 인증서 없음).
- benchmark: 70/120 실제 수집, snapshot VM 50셀 미수집.

자동 계약 13/13은 릴리스 안전 구조가 존재한다는 증거이지 논문 가설이나 실제 장치 성능의 증거가
아니다. 외부 서명 인증서, snapshot VM, 사용자 동의 장치 검증과 참가자 데이터가 남아 있다.
