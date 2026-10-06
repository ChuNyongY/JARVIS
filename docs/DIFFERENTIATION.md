# 기존 JARVIS 프로젝트 대비 차별성

## 비교 원칙

JARVIS라는 이름의 공개 프로젝트는 매우 많고 기능도 빠르게 바뀐다. 따라서 특정 저장소에 기능이 “없다”고 단정하지 않고, 공개 문서와 코드에서 확인한 항목만 비교해야 한다. 비교 대상은 다음 네 범주로 관리한다.

1. 상시대기 Windows 음성 비서: wake word, STT/TTS, LLM tool loop, 통합 기능
2. local-first 개인 비서: 로컬 모델, 개인 메모리, 개인정보 보호, MCP·브라우저 도구
3. Gemini 기반 Windows 비서: 실시간 음성, Vision, PowerShell, 앱·메시지 제어
4. desktop-agent 연구 시스템: 계획, UIA+Vision, 다중 앱 실행, 상태 기반 평가

이 기능들은 JAVIS에도 필요하지만 그 자체가 연구적 신규성은 아니다.

아래 비교는 2026-09-22에 공개 GitHub README를 다시 확인한 결과다. 저장소 전체의 부재를
README만으로 단정하지 않으며, “확인되지 않음”은 “구현되어 있지 않음”과 다르다.

## 현재 공개 구현과의 구체 비교

| 공개 저장소 | README에서 확인된 강점 | JAVIS가 그대로 경쟁하지 않는 부분 | JAVIS에서 별도로 검증하는 부분 |
|---|---|---|---|
| [Saic2605/jarvis](https://github.com/Saic2605/jarvis) | Claude tool loop, Windows HUD, edge-tts, Obsidian memory | 대화 UX·HUD·TTS·범용 도구 수 | action별 risk floor, exact approval binding, postcondition loop |
| [dushyant958/J.A.R.V.I.S](https://github.com/dushyant958/J.A.R.V.I.S) | LiveKit STT/LLM/TTS, FastMCP, screenshot·click·shell, Supabase/Redis memory | 실시간 음성·광범위 desktop tool | shell text 대신 typed capability, 각 action 직전 재정책 검사 |
| [ndunl075/Jarvis](https://github.com/ndunl075/Jarvis) | 완전 로컬 wake/VAD/STT/TTS/LLM, barge-in, DPAPI key, prompt-injection 경고, 상관 로그 | 로컬 완성도·UI·배포·TTS는 이 프로젝트가 앞섬 | addressedness/risk 결합 실험, exact-action token, 독립 OS postcondition |
| [aviarytech/jarvis / OpenDex](https://github.com/aviarytech/jarvis) | 다중 provider, permission gate, opt-in computer-use, OS keychain, signed release | 제품형 permission UX·theme·provider 생태계 | permission 기억보다 action/인자/대상/TTL/nonce에 묶인 승인 |
| [samonti86/jarvis](https://github.com/samonti86/jarvis) | always-on local wake/STT, 36-tool loop, TTS, vision, sandbox | 도구 폭·음성 출력·vision·운영 규모 | deterministic policy와 verifier를 LLM tool loop 밖에 둔 연구 구조 |
| [dmrr35/Open.Jarvis](https://github.com/dmrr35/Open.Jarvis) | local-first routing, provider redaction/fallback, plugin permission, path/URL safety, release checks | UI·plugin·provider 운영 기능 | 위험 하한·승인 TOCTOU·bounded retry/replan을 하나의 측정 가능한 폐루프로 결합 |

따라서 “기존 JARVIS는 안전하지 않고 JAVIS만 안전하다”는 주장은 하지 않는다. 현재 공개
프로젝트에도 permission gate, DPAPI, prompt-injection 경고, path/URL 검증, sandbox가 존재한다.
차이는 안전 기능의 유무보다, JAVIS가 이를 **typed plan → deterministic risk floor → exact-action
approval → OS postcondition**의 공통 계약으로 만들고 B0/B1/P1/P2에서 UER·Unsafe Execution·
Confirmation Burden을 함께 측정하려는 데 있다.

반대로 현재 JAVIS는 비교 프로젝트보다 TTS, barge-in, HUD, plugin 생태계, 도구 수, signed release,
장기 운영 경험이 부족하다. 실제 참가자 결과도 아직 없으므로 “더 안전하다”거나 “더 완성됐다”는
결론은 실험 전에는 주장할 수 없다.

## 비교 행렬

| 비교 축 | 일반 음성 비서 | Local-first 비서 | Gemini Windows 비서 | Desktop-agent 연구 | JAVIS의 초점 |
|---|---|---|---|---|---|
| Wake/STT/TTS | 일반적 | 강조됨 | 일반적 | 제품에 따라 다름 | 입력 계층으로 재사용 |
| LLM 도구 호출 | 일반적 | 일반적 | 일반적 | 연구 초점 | typed plan만 허용 |
| 앱·웹 제어 | 일반적 | 프로젝트별 상이 | 강조됨 | 연구 초점 | 제한된 MVP adapter |
| 메모리·개인화 | 프로젝트별 상이 | 강조됨 | 프로젝트별 상이 | 연구별 상이 | 기본 구현, 권한 근거로 사용 금지 |
| 확인·allowlist | 프로젝트별 상이 | 프로젝트별 상이 | 프로젝트별 상이 | 안전 연구에서 강조 | 모든 action의 공통 정책 계약 |
| 발화 대상성 | 프로젝트별 상이 | 대화 문맥에서 강조 | 프로젝트별 상이 | 연구별 상이 | 위험 정책과 결합해 측정 |
| 위험도 하한 | 프로젝트별 상이 | 프로젝트별 상이 | 프로젝트별 상이 | 안전 연구 초점 | 결정론적 capability floor |
| 승인 TOCTOU 방지 | 프로젝트별 상이 | 프로젝트별 상이 | 프로젝트별 상이 | 안전 연구 초점 | 대상·인자 결합 토큰 |
| 실행 후 검증 | 프로젝트별 상이 | 프로젝트별 상이 | 프로젝트별 상이 | 핵심 연구 주제 | postcondition 필수 |
| 안전성과 확인 부담의 동시 평가 | 프로젝트별 상이 | 프로젝트별 상이 | 프로젝트별 상이 | 벤치마크별 상이 | 논문의 주 평가 대상 |

## 실제 기여 주장

### C1. 발화 의도와 위험도를 결합한 실행 게이트

명령 분류 정확도만 측정하지 않고 addressedness·intent·target confidence의 최저값과 capability 위험 하한을 결합한다.

- 필요한 증거: 한국어 명령·일상대화·인용·가정·부정 발화 데이터셋
- 비교: B0 직접 실행, P1 Intent+Risk
- 반증 조건: UER이 감소하지 않거나 확인 부담 증가가 실용 한계를 넘음

### C2. LLM 밖의 결정론적 action 정책

LLM이 제안한 위험도보다 registry의 위험 하한을 우선하며, 권한 상승·외부 전송·민감정보 문맥은 위험도를 올리는 방향으로만 작동한다.

- 필요한 증거: 정책 truth table, property test, severe underclassification 결과
- 반증 조건: R3/R4가 R0/R1로 분류되거나 unknown capability가 통과함

### C3. 정확한 실행 대상에 결합된 승인

승인 토큰을 task/action/capability/arguments hash/target/risk/시간/nonce에 묶어 승인 후 대상 변경과 재사용을 차단한다.

- 필요한 증거: 인자·대상·위험도 변조, 만료, replay 테스트
- 반증 조건: 하나라도 변조된 action이 기존 승인으로 실행됨

### C4. 실행 결과를 OS 상태로 검증하는 폐루프

도구의 성공 문자열이 아니라 파일·프로세스·UI 상태의 postcondition으로 성공을 판단하고 재시도 횟수를 제한한다.

- 필요한 증거: 팝업, 지연, UI 이동, 네트워크 단절 등 고장 주입 실험
- 반증 조건: verifier가 실패를 성공으로 판정하거나 실패 뒤 무제한 반복함

## 실험 지표

- **UER** = 실제 action이 발생한 비대상·비명령 발화 / 전체 비대상·비명령 발화
- **Unsafe Execution Rate** = 필요한 승인을 거치지 않은 위험 action / 위험 action 요청
- **Severe Underclassification Rate** = 실제 R3/R4 중 R0/R1로 분류된 action / 실제 R3/R4
- **Task Success Rate** = 필수 postcondition을 모두 만족한 task / 전체 task
- **Recovery Rate** = 최초 실패 뒤 성공한 task / 복구 가능한 실패 task
- **Verification Precision** = 실제 성공이며 verifier도 성공인 task / verifier가 성공이라 판정한 task
- **Confirmation Burden** = 승인 요청 횟수 / 성공 task
- **Latency** = wake, STT, plan, approval-to-action, 전체 완료 구간별 시간

우월성은 사전에 선언하지 않는다. B0·B1·P1·P2를 같은 작업·초기 상태·평가기에서 반복 측정한 뒤에만 주장한다.

## 선행 프로젝트 검토 체크리스트

- 저장소 URL, 비교한 commit SHA, 확인 날짜 기록
- README뿐 아니라 정책·executor·approval·test 코드 확인
- 라이선스와 NOTICE 요구사항 확인
- 아이디어, 직접 사용한 코드, 재구현한 코드를 구분
- 코드 복사 시 원저작권·라이선스 보존
- “최초”, “유일”, “완전 안전” 같은 검증 불가능한 표현 금지
