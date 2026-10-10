# JAVIS 스킬 런타임: 생성·검색·사용·갱신 구조

## 프로젝트의 중심

JAVIS의 중심은 단순히 음성 명령을 도구 호출로 바꾸는 것이 아니다. 사용자가 반복하는 PC 작업을
**스킬**로 축적하고, 새 요청에서 관련 스킬을 찾아 사용하며, 실제 결과를 기록해 다음 버전을
갱신하는 Windows 에이전트다.

```text
음성/텍스트 요청
  -> Intent Gate
  -> 검증된 스킬 검색
  -> 선택된 스킬의 절차·허용 capability를 Planner에 제공
  -> typed Plan
  -> Policy / 정확한 승인 / Executor / OS Verifier
  -> 결과 기록
  -> 실패·성공 근거를 가진 새 스킬 초안 또는 개정 초안
  -> 사용자 승인 후 다음 버전 활성화
```

스킬은 실행 파일, 임의 Python 코드, PowerShell 명령을 저장하지 않는다. 스킬은 **절차와 기존
capability의 허용 목록**만 가진다. 실제 실행 권한은 기존 Capability Registry, Policy Engine,
Approval Token, Verifier가 계속 통제한다.

## 네 가지 동작

1. **생성**: 사용자가 새 반복 업무를 설명하거나 성공한 작업을 저장하고 싶다고 요청하면
   `SkillProposal`을 DRAFT로 만든다. 아직 실행에 쓰이지 않는다.
2. **검색**: 새 요청에서 로컬 카탈로그는 VERIFIED 상태의 스킬만 제목·설명·호출 문구로 검색한다.
3. **사용**: `SkillAwarePlanner`는 가장 관련 있는 검증 스킬 하나를 Planner에 제공한다. Planner가
   스킬의 허용 capability 밖의 행동을 제안하면 Plan은 거부된다.
4. **갱신**: 실행 결과는 `SkillUseRecord`로 버전과 함께 저장한다. 절차 변경은 기존 스킬을 덮어쓰지
   않고 새 DRAFT revision을 만든다. 사용자가 승인할 때만 새 버전이 VERIFIED가 되고 이전 버전은
   RETIRED가 된다.

## 예시: “회의 자료 PDF 열어 줘”

```text
skill: meeting-pdf-open / VERIFIED / v3
trigger: 회의 자료, PDF 열기
procedure:
  1. 허용된 회의 자료 폴더에서 후보를 읽는다.
  2. 대상이 하나가 아니면 사용자에게 고르게 한다.
  3. 선택된 PDF의 hash를 확인한다.
  4. 등록된 PDF viewer로 연다.
allowed capability: file.list, file.open
```

이 스킬이 있다고 해서 PDF를 자동으로 아무거나 열거나 파일을 삭제할 수 있는 것은 아니다. `file.open`
adapter와 verifier가 대상 hash, 허용 root, viewer identity를 확인하고, 위험도에 따라 정책과 승인을
다시 적용한다.

## 안전 원칙

- DRAFT 스킬은 검색·실행되지 않는다.
- 스킬은 Capability Registry에 없는 기능을 추가할 수 없다.
- LLM이 스킬 밖 capability를 포함한 Plan을 제안하면 거부한다.
- 실행 결과는 스킬의 "성공" 주장이 아니라 독립 verifier 결과로 기록한다.
- 자동 갱신은 새로운 권한이나 행동을 즉시 활성화하지 않는다. 새 초안으로만 저장된다.
- STOP, 승인 대상·인자 binding, 실행 후 OS 검증은 모든 스킬보다 우선한다.

## 현재 구현 상태

`skill_runtime.py`에는 로컬 SQLite 기반 `LocalSkillCatalog`, DRAFT/VERIFIED/RETIRED lifecycle,
검색, 사용 기록, revision, `SkillAwarePlanner` capability binding이 구현되어 있다.

다음 단계는 음성 UI에서 “이 작업을 스킬로 저장할까요?”라는 생성·승인 대화, 성공 task에서
초안 생성, 그리고 결과 기록을 바탕으로 LLM이 **개정 초안만** 제안하는 화면을 연결하는 것이다.
