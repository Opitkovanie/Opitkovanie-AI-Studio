#!/bin/bash

# Ten skrypt automatycznie uruchamia aplikację AI ViralCutter na komputerach Mac.

# 1. Pobierz absolutną ścieżkę do folderu, w którym znajduje się ten plik
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# 2. Przejdź do tego folderu
cd "$DIR"

# 3. Wyczyść ekran terminala dla lepszego efektu
clear

# 4. Wyświetl przyjazny komunikat
echo "===================================================="
echo "🚀 Uruchamianie AI ViralCutter by Opitkovanie..."
echo "===================================================="
echo "Proszę czekać. Aplikacja otworzy się w przeglądarce..."
echo ""

# 5. Uruchom aplikację z lokalnego środowiska, jeśli zostało utworzone przez Install.command
if [ -x "$DIR/.venv/bin/python" ]; then
    "$DIR/.venv/bin/python" -m streamlit run app.py
else
    python3.11 -m streamlit run app.py
fi
