# GitHub JARVIS 계열과의 차이

최초 확인일은 2026-10-03이고 공식 README 재확인일은 2026-10-05이다. GitHub에는 동일·유사 이름의 프로젝트가 여러 개라서 “현재 GitHub의
JAVIS AI”만으로 단일 저장소를 확정할 수 없다. 이 문서는 계획서와 가장 가까운 Windows 11,
상시 음성, 로컬 모델, 실제 PC 도구 구성을 가진
[`D3v0ps/JARVIS`](https://github.com/D3v0ps/JARVIS)를 주 비교 대상으로 삼는다. 기본적으로 행동을
우선하지만 irreversible action에는 사람 확인 gate를 둔
[`Axx33284/jarvis`](https://github.com/Axx33284/jarvis)는 안전성 방향을 확인하는 보조 기준으로만
사용한다.

2026-10-05 재확인에서 D3v0ps/JARVIS는 계속 Ollama/Qwen3, openWakeWord, Silero VAD,
faster-whisper, Kokoro/Piper/SAPI, WebView2/tkinter UI, repair installer와 phone 경로를 문서화하고
있다. guarded PowerShell/file/power action은 음성 또는 UI 확인 뒤 실행하며, README 자체도 배포
installer가 상용 인증서로 서명되지 않았다고 명시한다. Axx33284/JARVIS는 `full`/`standard`/`strict`
gate, 사람만 가능한 승인, gate 오류 시 read-only fail-closed와 file undo journal을 제공한다. 동시에
arbitrary `cmd.exe`/PowerShell, model-written Python skill을 사용자 권한의 동일 프로세스에서 실행하고
OS sandbox가 없으며, timeout된 skill thread를 종료할 수 없고 audit에 tool argument 원문이 남는다고
명시한다. 따라서 아래 차이는 단순히 “안전 기능 유무”가 아니라 승인 binding, 실행 격리, 독립
postcondition, 개인정보 최소화와 실험 증거의 강도 차이다.

## 결론

두 프로젝트는 경쟁 관계라기보다 최적화 목표가 다르다. GitHub JARVIS는 의존성 다운로드·자동
모델 선택·repair·phone까지 연결된 **제품형 완전 로컬 비서**에 가깝고, JARVIS-PC는 설치 가능한
Windows host를 갖췄지만 잘못 들은 말과 위험한 도구 호출을 어떻게 차단·승인·검증할지 측정하는
**안전 실행 제어 및 연구형 에이전트**에 가깝다.

| 비교 축 | D3v0ps/JARVIS | 현재 JARVIS-PC |
|---|---|---|
| 기본 모델 | 로컬 Ollama/Qwen3 tool calling | NIM typed planner와 로컬 gate; NIM 사용 시 목표 텍스트가 외부 API로 전송됨 |
| 음성 제품성 | openWakeWord → Silero VAD → faster-whisper → Kokoro/Piper/SAPI가 사용자 앱에 통합 | 같은 계열의 로컬 wake/STT/TTS 경계와 always-on runtime이 있으나 실제 한국어 장시간 사용자 검증은 미완료 |
| UI·배포 | 설치/repair EXE, WebView2/tkinter HUD, tray, 선택 autostart, phone UI | privacy-minimized HUD, native tray, 명시적 autostart, manifest 검증 per-user Setup EXE; 자동 dependency/model repair·phone UI는 없음 |
| 도구 폭 | 앱, 웹·날씨, timer/reminder, phone, clipboard, PowerShell, 파일, 전원 제어 | 등록 앱, exact-host HTTPS 탐색, 파일, PDF, 볼륨, 설정, Notepad, fixture/DOCX, memory, storage 중심의 제한된 capability |
| 위험 모델 | safe/announced/guarded와 고정 거부 목록; guarded는 음성 confirm을 기다림 | R0~R4 결정론 정책, target/arguments에 결합된 만료 승인 토큰, R4 strong-auth 채널 |
| 명령 실행 | guarded PowerShell을 30초 제한으로 제공 | 임의 shell을 제공하지 않고 고정 read-only PowerShell adapter만 허용 |
| 실행 후 검증 | 도구 결과와 UI에 성공을 보고 | executor와 분리된 verifier가 OS/파일/hash/PID/영수증을 다시 읽어 postcondition 확인 |
| 메모리 | `memory.json` 사실을 prompt에 주입 | bounded SQLite FTS, source/confidence/sensitivity/expiry, credential-like 값 거부 |
| 로그·개인정보 | transcript, tool arguments, refusals, latency를 로그에 기록한다고 문서화 | chain-hashed JSONL, credential redaction, 여러 adapter의 path/content 비공개 영수증 |
| 위험 시험 | 실제 PC에서 guarded power/file/PowerShell 도구 제공 | 영구삭제·종료·설치·reset commit은 snapshot VM 전용; 서명된 복원 증거 없이는 미완료 처리 |
| 연구 평가 | 안전 gate 중심 pytest와 사용자 기능 시나리오 | S01~S15 격리 manifest, B0/B1/P1/P2 exact-grid collector·condition runner·P2 recovery runner·서명된 snapshot-cell consumer·분석기, 현재 737 passed/3 skipped·83% coverage, 현재-PC 70/70셀 수집 |

보조 기준 Axx33284/JARVIS와 비교하면 양쪽 모두 human approval과 fail-closed gate를 갖지만,
JARVIS-PC는 임의 shell과 runtime-generated Python skill을 제품 capability에서 제외하고 action의
capability/target/arguments hash에 승인 토큰을 결합한다. 또 executor의 성공 보고와 별개로 OS·파일·
PID·hash를 다시 읽고, 위험 시험은 snapshot 복원·서명·artifact binding이 없으면 observation으로
기록하지 않는다. 반대로 Axx33284/JARVIS는 로컬 Ollama, system-audio hearing, 실제 screen control,
self-improving skill library와 폭넓은 일상 도구에서 더 완성된 제품 범위를 제공한다.

## JARVIS-PC가 더 강한 부분

1. **승인 재사용 방지**: 승인 토큰이 task/action/capability/arguments hash/target/risk/만료/nonce에
   결합되어, “확인” 한 번을 다른 대상이나 인자로 재사용할 수 없다.
2. **독립 사후조건**: 볼륨, 프로세스, PDF, 파일 이동·휴지통, DOCX, package identity, message
   receipt 등을 executor 보고만 믿지 않고 별도 verifier가 다시 확인한다.
3. **위험 시험 격리**: 영구삭제·shutdown·reset·install을 개인 PC에서 시험 성공으로 간주하지
   않으며 snapshot restore와 signed evidence를 요구한다.
4. **발화 전 게이트**: STOP과 비호명·가정 발화, untrusted web content를 planner 호출 전에
   차단하는 것이 연구의 핵심 독립변수다.
5. **감사 가능한 실험 구조**: 고정 시나리오, evidence level, 금지 효과, paired baseline 통계를
   코드 계약으로 둔다.

## GitHub JARVIS가 더 강한 부분

1. 설치가 Python/Ollama/모델 선택·다운로드·바로가기·repair까지 처리하는 완성형 온보딩이다.
2. WebView2 UI, phone, timer/reminder, web/weather 등 일상 사용 기능과 표현력이 훨씬 넓다.
3. Ollama 기반으로 LLM 추론도 로컬이며 API key가 필요 없다.
4. Kokoro 음성, 실제 microphone/GPU fallback, 사용자용 troubleshooting이 제품 흐름에 연결돼
   있다.
5. 현재 JARVIS-PC의 exact-host 새 창 탐색보다 넓은 웹 검색·콘텐츠 처리, phone remote,
   clipboard, media와 다양한 도구가 이미 한 assistant loop에 통합돼 있다.

## 졸업작품 차별점 문장

> 기존 GitHub JARVIS 계열이 로컬 음성 비서의 기능 폭과 사용자 경험을 중심으로 구성되는 반면,
> JARVIS-PC는 발화 addressedness, 결정론적 위험 분류, payload-bound 승인, 최소권한 capability,
> 독립 사후조건 검증과 snapshot-VM 위험 시험을 하나의 폐루프로 결합하고 그 안전 효과를
> B0/B1/P1/P2 조건에서 정량 평가한다.

이 차별점은 UI가 더 화려하거나 도구 수가 더 많다는 주장이 아니다. 논문에서 방어 가능한 기여는
“기능 수”가 아니라 **오인식·무승인·과권한 실행과 검증되지 않은 성공 보고를 얼마나 줄였는가**다.

## 근거와 한계

- D3v0ps README는 openWakeWord, Silero VAD, faster-whisper, Ollama/Qwen3, Kokoro, HUD와
  `memory.json` 구성을 설명한다.
- 같은 README는 PowerShell/file/power를 guarded로 두고 음성 confirm을 기다리며, 특정 위험 명령은
  항상 거부한다고 설명한다. 또한 transcript와 tool arguments가 로그에 기록된다고 명시한다.
- Axx33284 README는 로컬 Windows 도구 73개, standard에서 irreversible 확인, strict에서 모든
  destructive 확인, full에서 무확인을 선택하는 gate와 arbitrary shell의 비-sandbox 한계를 명시한다.
  JARVIS-PC는 사용자 설정과 무관하게 risk별 최소 승인 강도를 낮출 수 없다는 점에서 대비된다.
- 본 비교는 공개 README와 저장소 구조를 기준으로 한 것이다. 상대 저장소의 모든 구현 라인을
  보안 감사하거나 실제 하드웨어에서 재현한 결과는 아니다.
