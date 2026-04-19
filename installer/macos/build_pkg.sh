#!/usr/bin/env bash
# ============================================================
#  Ghost — macOS installer builder
#
#  Pipeline:
#    1. Build Ghost.app via py2app (setup_mac.py)
#    2. Wrap Ghost.app in a component pkg (pkgbuild)
#    3. Download BlackHole 2ch pkg from official GitHub release
#    4. Combine both into a single distribution pkg (productbuild)
#
#  Output: installer/macos/Output/GhostInstaller-<version>.pkg
#
#  Run from project root OR from installer/macos/.
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${ROOT}"

# Pull version from src/version.py (single source of truth).
VERSION="$(python3 -c "import re,sys; t=open('src/version.py').read(); print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', t).group(1))")"
echo "[mac-build] Ghost v${VERSION}"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "[mac-build] ERROR: this script must run on macOS." >&2
    exit 1
fi

OUT="${HERE}/Output"
BUILD="${HERE}/build"
mkdir -p "${OUT}" "${BUILD}"

# ---------------------------------------------------------------
# 1. Icons + .icns
# ---------------------------------------------------------------
echo "[mac-build] 1/5  regenerating icons..."
python3 scripts/make_icons.py
iconutil -c icns assets/icon.iconset -o assets/icon.icns

# ---------------------------------------------------------------
# 2. py2app — build Ghost.app
# ---------------------------------------------------------------
echo "[mac-build] 2/5  building Ghost.app..."
rm -rf build dist
python3 setup_mac.py py2app
APP_BUNDLE="dist/Ghost.app"
[ -d "${APP_BUNDLE}" ] || { echo "[mac-build] ERROR: Ghost.app not produced"; exit 1; }

# ---------------------------------------------------------------
# 3. pkgbuild — wrap the app in a component pkg
# ---------------------------------------------------------------
echo "[mac-build] 3/5  wrapping Ghost.app into component pkg..."
pkgbuild \
    --root "dist" \
    --identifier "io.github.userjesus.ghost" \
    --version "${VERSION}" \
    --install-location "/Applications" \
    --component-plist /dev/stdin \
    "${BUILD}/Ghost.pkg" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<array>
  <dict>
    <key>BundleIsRelocatable</key><false/>
    <key>BundleIsVersionChecked</key><true/>
    <key>BundleOverwriteAction</key><string>upgrade</string>
    <key>RootRelativeBundlePath</key><string>Ghost.app</string>
  </dict>
</array>
</plist>
PLIST

# ---------------------------------------------------------------
# 4. Fetch BlackHole 2ch driver pkg
# ---------------------------------------------------------------
echo "[mac-build] 4/5  fetching BlackHole 2ch driver..."
BLACKHOLE_CACHE="${BUILD}/BlackHole2ch.pkg"
if [ ! -s "${BLACKHOLE_CACHE}" ]; then
    # Resolve latest release URL from GitHub API
    BH_URL="$(curl -s https://api.github.com/repos/ExistentialAudio/BlackHole/releases/latest \
        | grep -o 'https://[^"]*BlackHole2ch[^"]*\.pkg' | head -1 || true)"
    if [ -z "${BH_URL}" ]; then
        echo "[mac-build] WARNING: could not auto-detect BlackHole URL — using fallback."
        BH_URL="https://existential.audio/downloads/BlackHole2ch.v0.6.0.pkg"
    fi
    echo "           → ${BH_URL}"
    curl -L -o "${BLACKHOLE_CACHE}" "${BH_URL}"
fi

# Optional: verify BlackHole signature (Apple-notarized) — non-fatal.
if ! pkgutil --check-signature "${BLACKHOLE_CACHE}" >/dev/null 2>&1; then
    echo "[mac-build] WARNING: BlackHole pkg signature verification failed."
fi
cp "${BLACKHOLE_CACHE}" "${BUILD}/BlackHole2ch.pkg"

# ---------------------------------------------------------------
# 5. productbuild — combine into distribution pkg
# ---------------------------------------------------------------
echo "[mac-build] 5/5  combining into single installer..."

# Plain-text license extracted from LICENSE for the installer pane.
sed -n '1,/=========/p' LICENSE | head -n -2 > "${HERE}/Resources/license.txt"

OUT_PKG="${OUT}/GhostInstaller-${VERSION}.pkg"
productbuild \
    --distribution "${HERE}/distribution.xml" \
    --package-path "${BUILD}" \
    --resources "${HERE}/Resources" \
    --version "${VERSION}" \
    "${OUT_PKG}"

echo
echo "[mac-build] done."
echo "           Output: ${OUT_PKG}"
echo "           Size:   $(du -h "${OUT_PKG}" | cut -f1)"

# ---------------------------------------------------------------
# Optional: wrap in a DMG with the uninstaller script alongside.
# ---------------------------------------------------------------
DMG_OUT="${OUT}/Ghost-${VERSION}.dmg"
STAGING="${BUILD}/dmg-staging"
mkdir -p "${STAGING}"
cp "${OUT_PKG}"             "${STAGING}/Ghost Installer.pkg"
cp "${ROOT}/scripts/uninstall_mac.sh" "${STAGING}/uninstall_mac.sh"
cp "${ROOT}/README.md"      "${STAGING}/README.md"
cp "${ROOT}/LICENSE"        "${STAGING}/LICENSE.txt"
chmod +x "${STAGING}/uninstall_mac.sh"

if command -v hdiutil >/dev/null 2>&1; then
    rm -f "${DMG_OUT}"
    hdiutil create \
        -volname "Ghost ${VERSION}" \
        -srcfolder "${STAGING}" \
        -ov -format UDZO \
        "${DMG_OUT}"
    echo "           DMG:    ${DMG_OUT}"
fi
