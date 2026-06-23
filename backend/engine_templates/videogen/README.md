# Local LTX Studio

Lokalny panel Streamlit dla LTX 2.3 na Apple Silicon. Domyślnie używa `dgrauet/ltx-2.3-mlx-q4`, czyli wariantu MLX int4 polecanego dla Maców z mniejszą pamięcią.

## Uruchomienie

Najprościej na nowym Macu:

```text
Kliknij: Install.command
Kliknij: Start Local LTX Studio.command
```

Ręcznie w terminalu:

```bash
./scripts/setup.sh
uv run python scripts/download_models.py
./scripts/run.sh
```

Aplikacja startuje pod adresem:

```text
http://127.0.0.1:8501
```

Pierwsze generowanie może potrwać długo, bo model i text encoder są pobierane z Hugging Face.
Downloader pobiera tylko pliki potrzebne do T2V/I2V, zamiast pełnego snapshotu repo modelu.

## Wymagania

- Mac z Apple Silicon.
- macOS z Homebrew albo możliwością zainstalowania Homebrew przez `Install.command`.
- Python `3.11` lub nowszy. Instalator używa `uv python install 3.11`, więc nie trzeba mieć Pythona wcześniej.
- `uv`, `ffmpeg` i `ffprobe`. `Install.command` instaluje je przez Homebrew.
- Pythonowe zależności aplikacji są w `pyproject.toml` oraz pomocniczo w `requirements.txt`, a zależności LTX MLX w `vendor/ltx-2-mlx/pyproject.toml` i jego pakietach workspace.

## Zalecany start dla M4 Pro 24 GB

- Model: `dgrauet/ltx-2.3-mlx-q4`
- Rozdzielczość: `512 x 320`
- Długość: `49` albo `65` klatek
- Kroki: `8`
- Prompt enhancer: wyłączony na pierwsze testy

Potem można podnosić rozdzielczość albo liczbę klatek, patrząc na zużycie pamięci i czas renderu.

UI pokazuje tylko warianty Q4, bo to jedyna sensowna klasa LTX 2.3 dla MacBooka z 24 GB RAM. Q8/BF16 są celowo pominięte.

Opcjonalnie można pobrać eksperymentalny Q8:

```bash
uv run python scripts/download_models.py --model q8
```

Q8 może poprawić jakość, ale na 24 GB RAM używaj krótkich klipów i niskich rozdzielczości.
