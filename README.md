# DubCut Studio

DubCut Studio łączy **DubMaster** i **AI ViralCutter** w jednej natywnej aplikacji desktopowej
(React + Electron) z lokalnym backendem Python (FastAPI). **Bez Streamlit.** Całe przetwarzanie
odbywa się lokalnie na komputerze użytkownika.

## Architektura

- `src` — natywny interfejs React (OmniVoice-style): Pulpit, DubMaster, AI ViralCutter, Ustawienia.
- `backend` — natywny serwer FastAPI (`server.py`). Bez Streamlit. Reużywa silników z `vendor/`
  przez lekki shim `shims/streamlit`, udostępnia konfigurację, presety, czcionki, języki oraz
  uruchamia pipeline'y jako zadania ze strumieniem postępu (SSE).
- `electron` — launcher desktopowy: kopiuje silniki do zapisywalnego katalogu danych użytkownika,
  uruchamia backend (jeden lokalny port) i okno aplikacji.
- `vendor/DubMaster`, `vendor/ShortsGenerator` — źródłowe silniki (download, Whisper, Gemini,
  ffmpeg, Demucs, Qwen-TTS, YOLO). Pakowane razem z aplikacją, ich logika jest reużywana.

Oryginalne foldery `DubMaster`, `ShortsGenerator` i `OmniVoice Studio` **nie są modyfikowane**.

## Uruchomienie developerskie

```bash
npm install
npm run dev        # Vite + Electron
```

Backend uruchamia się automatycznie po instalacji środowiska (patrz niżej). Domyślny port:
`http://127.0.0.1:8765`.

## Instalacja silników (w aplikacji)

Po starcie wejdź w **Ustawienia → Zainstaluj silniki**. Instalator:

1. tworzy jedno środowisko `venv` w katalogu danych użytkownika,
2. instaluje warstwę API (FastAPI/uvicorn) — backend startuje od razu,
3. instaluje pełny stos ML (`backend/requirements.txt`: torch, whisper, google-genai, demucs,
   qwen-tts, ultralytics, …).

Instalator: `backend/install.sh` (macOS/Linux) oraz `backend/install.ps1` (Windows). Wymaga
Python 3.11+ i `ffmpeg` w PATH.

## Budowanie instalatora

```bash
npm run dist
```

Tworzy dystrybucję przez electron-builder: **DMG** (macOS), **NSIS** (Windows), **AppImage**
(Linux). `backend/` i `vendor/` są dołączane jako zasoby.

## API backendu (skrót)

- `GET /api/health` — status + wykryte zależności
- `GET/POST /api/config` — wczytanie/zapis ustawień (schematy DubMaster + ShortsGenerator)
- `GET /api/meta` — presety napisów, czcionki, języki, głosy, animacje
- `POST /api/shorts/analyze`, `POST /api/dub/run` — start pipeline'u → `{ job_id }`
- `GET /api/jobs/{id}` / `GET /api/jobs/{id}/events` (SSE) / `POST /api/jobs/{id}/cancel`
