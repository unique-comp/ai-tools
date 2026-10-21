#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then echo 'A macOS computer is required.' >&2; exit 1; fi
export HOMEBREW_NO_AUTO_UPDATE=1
brew install --overwrite python@3.12 python-tk@3.12 tesseract tesseract-lang
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
"$PYTHON" -m venv .build-venv
PYTHON="$PWD/.build-venv/bin/python"
"$PYTHON" -m pip install -r requirements-build.txt 'reportlab>=4,<5'
"$PYTHON" -m unittest -v test_tracker
OCR_ROOT="$(brew --prefix tesseract)"
LANG_ROOT="$(brew --prefix)/share/tessdata"
test -s "$LANG_ROOT/ara.traineddata"
test -s "$LANG_ROOT/eng.traineddata"
export MACOSX_DEPLOYMENT_TARGET=15.0
"$PYTHON" -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name 'Ajeer Tracker' --osx-bundle-identifier com.uniqueinnovations.ajeertracker \
  --collect-all pypdfium2_raw --collect-data pypdfium2 \
  --add-binary "$OCR_ROOT/bin/tesseract:ocr" \
  --add-data "$LANG_ROOT/eng.traineddata:ocr/tessdata" \
  --add-data "$LANG_ROOT/ara.traineddata:ocr/tessdata" ajeer_tracker.py
APP="$PWD/dist/Ajeer Tracker.app"
EXE="$APP/Contents/MacOS/Ajeer Tracker"
WORK="$(mktemp -d -t ajeer-build)"
"$EXE" --runtime-check "$WORK/runtime.json"
"$EXE" --smoke-ui "$WORK/ui"
test -s "$WORK/runtime.json"
test -s "$WORK/ui/ui-smoke-ok.txt"
"$PYTHON" verify_bundle.py "$APP"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"
ARCH="$("$PYTHON" -c 'import platform; print(platform.machine())')"
mkdir -p deliverables
/usr/bin/ditto -c -k --keepParent "$APP" "deliverables/Ajeer Tracker - macOS $ARCH.zip"
cp STAFF-GUIDE.txt deliverables/
cp "$WORK/runtime.json" "deliverables/runtime-$ARCH.json"
echo "Built and checked: $PWD/deliverables/Ajeer Tracker - macOS $ARCH.zip"
