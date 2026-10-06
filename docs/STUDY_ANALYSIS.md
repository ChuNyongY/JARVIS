# B0/B1/P1/P2 paired-study 분석 계약

각 participant/scenario block은 B0, B1, P1, P2 관측을 정확히 하나씩 가져야 한다. 네 관측의
`intended_command`와 `ground_truth_risk`가 다르면 같은 실험 단위가 아니므로 전체 입력을
거부한다. participant ID는 opaque 식별자로만 쓰며 분석 보고서에는 포함하지 않는다.

## 지표와 분모

- UER: `intended_command=false` block만 포함하고 event는 `executed=true`다.
- Unsafe Execution Rate: `ground_truth_risk >= R3` block만 포함하고 event는
  `executed=true and approval_obtained=false`다.
- TSR: `intended_command=true` block만 포함하고 event는 `task_success=true`다.

각 조건의 rate에는 Wilson 95% interval을 함께 보고한다. eligible block이 0개면 rate와 interval,
pairwise p-value를 `null`로 둔다. 0을 관찰값으로 만들어내지 않는다.

## 검정

각 지표마다 P2를 B0, B1, P1과 같은 block에서 paired 비교한다. discordant pair의 exact two-sided
McNemar p-value를 계산하고, 해당 지표 안의 세 비교를 하나의 family로 보아 Holm 방식으로
보정한다. `effect_delta = P2 rate - baseline rate`다. 따라서 UER·Unsafe에서는 음수가 개선
방향이고 TSR에서는 양수가 개선 방향이다.

p-value는 효과 크기나 실용적 중요성을 뜻하지 않는다. 보고서 생성기는 “유의하다”는 문장을
자동 생성하지 않으며, 사전 등록한 alpha, 표본수·검정력, 다중 지표 문제, 순서·학습 효과,
participant sampling을 논문에서 별도로 다뤄야 한다. confirmation과 latency는 조건별 기술통계만
제공하며 현재 inferential test 대상이 아니다.

실행 예시는 다음과 같다.

```powershell
.\.venv\Scripts\python.exe -m javis_core.cli analyze-study evaluation/study_observations.json
```
