#!/bin/zsh
set -euo pipefail

# Build the native OpenRelix macOS client. Unlike build_macos_client.sh (a
# WKWebView shell around reports/panel.html), this client is a pure SwiftUI app
# that decodes reports/overview-data.json and renders every section natively.

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
APP_NAME="OpenRelix Native"
APP_EXECUTABLE="OpenRelixNative"
BUNDLE_ID="${OPENRELIX_NATIVE_BUNDLE_ID:-io.github.openrelix.OpenRelixNative}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT_PATH="${OPENRELIX_NATIVE_OUTPUT:-$REPO_ROOT/dist/OpenRelixNative.app}"
APP_ICON_SOURCE="${OPENRELIX_APP_ICON_SOURCE:-$REPO_ROOT/macos/OpenRelixClient/AppIcon.png}"
APP_ICON_BASENAME="OpenRelixAppIcon"
STATE_ROOT="${AI_ASSET_STATE_DIR:-}"
OPEN_AFTER=0
SELFTEST=0

usage() {
  cat <<'EOF'
Usage: scripts/build_macos_native.sh [options]

Build the native (SwiftUI, no web view) OpenRelix macOS client.

Options:
  --output PATH       Write the .app bundle to PATH.
  --state-root PATH   Embed the OpenRelix state root used by the app.
  --icon PATH         Use PATH as the source PNG for the app icon.
  --open              Open the app after building.
  --selftest          After building, run the headless decoder self-test.
  -h, --help          Show this help.
EOF
}

make_app_icon() {
  local source="$1" iconset="$2" output="$3"
  local icon_specs=(
    "16 icon_16x16.png" "32 icon_16x16@2x.png" "32 icon_32x32.png"
    "64 icon_32x32@2x.png" "128 icon_128x128.png" "256 icon_128x128@2x.png"
    "256 icon_256x256.png" "512 icon_256x256@2x.png" "512 icon_512x512.png"
    "1024 icon_512x512@2x.png"
  )
  local spec size filename
  rm -rf "$iconset"; mkdir -p "$iconset"
  for spec in "${icon_specs[@]}"; do
    size="${spec%% *}"; filename="${spec#* }"
    sips -z "$size" "$size" "$source" --out "$iconset/$filename" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$output"
  rm -rf "$iconset"
}

while (( $# )); do
  case "$1" in
    --output) OUTPUT_PATH="$2"; shift 2 ;;
    --output=*) OUTPUT_PATH="${1#--output=}"; shift ;;
    --state-root) STATE_ROOT="$2"; shift 2 ;;
    --state-root=*) STATE_ROOT="${1#--state-root=}"; shift ;;
    --icon) APP_ICON_SOURCE="$2"; shift 2 ;;
    --icon=*) APP_ICON_SOURCE="${1#--icon=}"; shift ;;
    --open) OPEN_AFTER=1; shift ;;
    --selftest) SELFTEST=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$OSTYPE" != darwin* ]]; then
  echo "The OpenRelix macOS client can only be built on macOS." >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "Missing swiftc. Install Xcode Command Line Tools first: xcode-select --install" >&2
  exit 1
fi

if [[ -z "$STATE_ROOT" ]]; then
  STATE_ROOT="$(
    PYTHONPATH="$REPO_ROOT/scripts" "$PYTHON_BIN" -c 'from asset_runtime import default_state_root; print(default_state_root())'
  )"
fi
APP_VERSION="$(
  PYTHONPATH="$REPO_ROOT/scripts" "$PYTHON_BIN" -c 'from asset_runtime import get_project_version; print(get_project_version())' 2>/dev/null || true
)"
[[ -z "$APP_VERSION" ]] && APP_VERSION="0.0.0"

SOURCE_FILE="$REPO_ROOT/macos/OpenRelixNative/main.swift"
[[ -f "$SOURCE_FILE" ]] || { echo "Missing client source: $SOURCE_FILE" >&2; exit 1; }

OUTPUT_PATH="${OUTPUT_PATH:A}"
STATE_ROOT="${STATE_ROOT:A}"
APP_ICON_SOURCE="${APP_ICON_SOURCE:A}"
CONTENTS_DIR="$OUTPUT_PATH/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

rm -rf "$OUTPUT_PATH"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

swiftc -O \
  -framework AppKit \
  -framework SwiftUI \
  "$SOURCE_FILE" \
  -o "$MACOS_DIR/$APP_EXECUTABLE"

chmod +x "$MACOS_DIR/$APP_EXECUTABLE"
printf '%s\n' "$STATE_ROOT" > "$RESOURCES_DIR/OpenRelixStateRoot.txt"
printf 'APPL????' > "$CONTENTS_DIR/PkgInfo"

if [[ -f "$APP_ICON_SOURCE" ]] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  make_app_icon "$APP_ICON_SOURCE" "$RESOURCES_DIR/$APP_ICON_BASENAME.iconset" "$RESOURCES_DIR/$APP_ICON_BASENAME.icns"
fi

cat > "$CONTENTS_DIR/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleExecutable</key><string>$APP_EXECUTABLE</string>
  <key>CFBundleIconFile</key><string>$APP_ICON_BASENAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
  <key>CFBundleVersion</key><string>$APP_VERSION</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$CONTENTS_DIR/Info.plist" >/dev/null
fi

if [[ "${OPENRELIX_SKIP_CODESIGN:-0}" != "1" ]] && command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$OUTPUT_PATH" >/dev/null 2>&1 || true
fi

echo "Built $OUTPUT_PATH"
echo "State root $STATE_ROOT"

if (( SELFTEST )); then
  echo "--- self-test ---"
  "$MACOS_DIR/$APP_EXECUTABLE" --selftest
fi

if (( OPEN_AFTER )); then
  open "$OUTPUT_PATH"
fi
