#!/usr/bin/env bash
# ============================================================
#  Ghost — macOS installer builder
#
#  Pipeline (PyInstaller — same tool as Windows):
#    1. make_icons.py → PNGs + iconset
#    2. iconutil      → assets/icon.icns
#    3. pyinstaller   → dist/Ghost.app
#    4. pkgbuild      → Ghost.pkg (component pkg, installs .app to /Applications)
#    5. curl          → fetch BlackHole 2ch driver pkg
#    6. productbuild  → GhostInstaller-<version>.pkg (combines the two)
#    7. hdiutil       → Ghost-<version>.dmg (pkg + uninstaller + README)
#
#  Output: installer/macos/Output/{GhostInstaller-*.pkg, Ghost-*.dmg}
#
#  Run from project root OR from installer/macos/.
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${ROOT}"

# Pull version from src/version.py.
VERSION="$(python3 -c "import re; t=open('src/version.py').read(); print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', t).group(1))")"
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
echo "[mac-build] 1/7  regenerating icons..."
python3 scripts/make_icons.py

echo "[mac-build] 2/7  icon.iconset -> icon.icns"
iconutil -c icns assets/icon.iconset -o assets/icon.icns

# ---------------------------------------------------------------
# 3. PyInstaller — build Ghost.app
# ---------------------------------------------------------------
echo "[mac-build] 3/7  building Ghost.app via PyInstaller..."
rm -rf build dist
pyinstaller --noconfirm --clean ghost_mac.spec
APP_BUNDLE="dist/Ghost.app"
[ -d "${APP_BUNDLE}" ] || { echo "[mac-build] ERROR: Ghost.app not produced"; ls -la dist || true; exit 1; }

# ---------------------------------------------------------------
# 4. pkgbuild — wrap the app in a component pkg
# ---------------------------------------------------------------
echo "[mac-build] 4/7  wrapping Ghost.app into component pkg..."
# pkgbuild needs a staging directory containing ONLY the .app
STAGE="${BUILD}/app-stage"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"
cp -R "${APP_BUNDLE}" "${STAGE}/"

pkgbuild \
    --root "${STAGE}" \
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
# 5. Fetch BlackHole 2ch driver pkg
# ---------------------------------------------------------------
echo "[mac-build] 5/7  fetching BlackHole 2ch driver..."
BLACKHOLE_CACHE="${BUILD}/BlackHole2ch.pkg"
if [ ! -s "${BLACKHOLE_CACHE}" ]; then
    # Try to resolve the latest release URL from GitHub; fall back to a pinned mirror.
    BH_URL="$(curl -fsS https://api.github.com/repos/ExistentialAudio/BlackHole/releases/latest 2>/dev/null \
        | grep -o 'https://[^"]*BlackHole2ch[^"]*\.pkg' | head -1 || true)"
    if [ -z "${BH_URL}" ]; then
        BH_URL="https://existential.audio/downloads/BlackHole2ch.v0.6.0.pkg"
    fi
    echo "           → ${BH_URL}"
    curl -L --retry 3 --retry-delay 2 -o "${BLACKHOLE_CACHE}" "${BH_URL}" || {
        echo "[mac-build] WARNING: BlackHole download failed — shipping installer without driver"
        BLACKHOLE_CACHE=""
    }
fi

# ---------------------------------------------------------------
# 6. productbuild — combine into distribution pkg
# ---------------------------------------------------------------
echo "[mac-build] 6/7  combining into single installer..."

# Plain-text LICENSE excerpt for the license pane.
sed -n '1,/=========/p' LICENSE | sed '$d' > "${HERE}/Resources/license.txt" || cp LICENSE "${HERE}/Resources/license.txt"

OUT_PKG="${OUT}/GhostInstaller-${VERSION}.pkg"

# If we failed to fetch BlackHole, fall back to a distribution without it.
if [ -z "${BLACKHOLE_CACHE}" ] || [ ! -s "${BLACKHOLE_CACHE}" ]; then
    # Standalone Ghost.pkg — rename + place.
    cp "${BUILD}/Ghost.pkg" "${OUT_PKG}"
    echo "           (BlackHole missing — shipped Ghost-only pkg)"
else
    # BlackHole is already at ${BUILD}/BlackHole2ch.pkg (that's where the curl
    # downloaded it); no need to copy. productbuild will find it via --package-path.
    productbuild \
        --distribution "${HERE}/distribution.xml" \
        --package-path "${BUILD}" \
        --resources "${HERE}/Resources" \
        --version "${VERSION}" \
        "${OUT_PKG}"
fi

echo
echo "[mac-build] pkg: ${OUT_PKG} ($(du -h "${OUT_PKG}" | cut -f1))"

# ---------------------------------------------------------------
# 7. DMG — wrap the pkg + uninstaller + README for distribution
# ---------------------------------------------------------------
DMG_OUT="${OUT}/Ghost-${VERSION}.dmg"
STAGING="${BUILD}/dmg-staging"
rm -rf "${STAGING}"
mkdir -p "${STAGING}"
cp "${OUT_PKG}"              "${STAGING}/Ghost Installer.pkg"
cp "${ROOT}/scripts/uninstall_mac.sh" "${STAGING}/uninstall_mac.sh"
cp "${ROOT}/README.md"       "${STAGING}/README.md"
cp "${ROOT}/LICENSE"         "${STAGING}/LICENSE.txt"
chmod +x "${STAGING}/uninstall_mac.sh"

rm -f "${DMG_OUT}"
hdiutil create \
    -volname "Ghost ${VERSION}" \
    -srcfolder "${STAGING}" \
    -ov -format UDZO \
    "${DMG_OUT}"

echo "[mac-build] dmg: ${DMG_OUT} ($(du -h "${DMG_OUT}" | cut -f1))"
echo "[mac-build] done."
