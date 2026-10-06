# Local faster-whisper model

이 directory는 Hugging Face의 `Systran/faster-whisper-tiny` 저장소 commit
`d90ca5fe260221311c53c58e660288d3deb8d356`에서 필요한 CTranslate2 파일 네 개만 받았다.
`manifest.json`이 filename, byte size와 SHA-256을 고정한다. runtime은 이 local directory만
사용하며 모델을 자동 다운로드하지 않는다.

원본 모델 카드와 라이선스: https://huggingface.co/Systran/faster-whisper-tiny
