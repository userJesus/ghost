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
# 3b. Code-signing
#
# NOTE: PyInstaller already performs internal ad-hoc code-signing on the
# bundle it produces (`Signing the BUNDLE...` in the build log). Re-signing
# on top of that can corrupt embedded Frameworks/dylibs and break the pkg
# payload extraction at install time. So here we only re-sign WHEN the user
# provides a real Apple Developer ID — for notarizable builds. For the
# default unsigned CI flow, we trust PyInstaller's signature and skip.
# ---------------------------------------------------------------
if [ -n "${DEVELOPER_ID_APPLICATION:-}" ]; then
    echo "[mac-build] 3b/ re-signing with Developer ID: ${DEVELOPER_ID_APPLICATION}"
    codesign --deep --force --sign "${DEVELOPER_ID_APPLICATION}" \
        --timestamp --options runtime --entitlements /dev/stdin "${APP_BUNDLE}" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.device.microphone</key><true/>
  <key>com.apple.security.device.camera</key><true/>
</dict>
</plist>
ENT
else
    echo "[mac-build] 3b/ keeping PyInstaller's ad-hoc signature (no Developer ID provided)"
fi

# ---------------------------------------------------------------
# 4. Fetch BlackHole 2ch driver pkg (fetched BEFORE pkgbuild so we can embed
#    it alongside the postinstall script).
# ---------------------------------------------------------------
echo "[mac-build] 4/7  fetching BlackHole 2ch driver..."
BLACKHOLE_CACHE="${BUILD}/BlackHole2ch.pkg"
if [ ! -s "${BLACKHOLE_CACHE}" ]; then
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
# 5. pkgbuild — Ghost.pkg with postinstall that installs BlackHole
#
# Instead of productbuild's multi-choice distribution (which had the "software
# not found" payload-resolution bug on some macOS versions), we embed the
# BlackHole pkg as a SCRIPTS-ARCHIVE RESOURCE alongside a postinstall script.
# Postinstall runs AFTER Ghost.app is copied to /Applications and invokes
# `installer -pkg BlackHole2ch.pkg -target /` to bring the driver in.
# ---------------------------------------------------------------
echo "[mac-build] 5/7  building Ghost.pkg with BlackHole postinstall..."

# Prepare a scripts directory pkgbuild will archive.
SCRIPTS_DIR="${BUILD}/scripts"
rm -rf "${SCRIPTS_DIR}"
mkdir -p "${SCRIPTS_DIR}"
cp "${HERE}/scripts/postinstall" "${SCRIPTS_DIR}/postinstall"
chmod +x "${SCRIPTS_DIR}/postinstall"
if [ -n "${BLACKHOLE_CACHE}" ] && [ -s "${BLACKHOLE_CACHE}" ]; then
    cp "${BLACKHOLE_CACHE}" "${SCRIPTS_DIR}/BlackHole2ch.pkg"
    echo "           bundled BlackHole2ch.pkg alongside postinstall"
else
    echo "           (no BlackHole bundled — postinstall will skip driver install)"
fi

pkgbuild \
    --component "${APP_BUNDLE}" \
    --scripts "${SCRIPTS_DIR}" \
    --identifier "io.github.userjesus.ghost" \
    --version "${VERSION}" \
    --install-location "/Applications" \
    "${BUILD}/Ghost.pkg"

echo "[mac-build]     Ghost.pkg size: $(du -h "${BUILD}/Ghost.pkg" | cut -f1)"

# ---------------------------------------------------------------
# 6. productbuild — wrap Ghost.pkg so the installer has the full wizard UX
#    (welcome → license → customize → install → conclusion). Ghost.pkg is the
#    only component; BlackHole is installed by its postinstall.
# ---------------------------------------------------------------
echo "[mac-build] 6/7  wrapping Ghost.pkg in distribution installer..."

# Plain-text LICENSE excerpt for the license pane.
sed -n '1,/=========/p' LICENSE | sed '$d' > "${HERE}/Resources/license.txt" || cp LICENSE "${HERE}/Resources/license.txt"

OUT_PKG="${OUT}/GhostInstaller-${VERSION}.pkg"
productbuild \
    --distribution "${HERE}/distribution.xml" \
    --package-path "${BUILD}" \
    --resources "${HERE}/Resources" \
    --version "${VERSION}" \
    "${OUT_PKG}"

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

# hdiutil is flaky on GitHub Actions macOS runners — sometimes a previous
# volume mount is still held ("Resource busy"). Pre-unmount + retry with
# exponential backoff handles both cases.
rm -f "${DMG_OUT}"
hdiutil detach "/Volumes/Ghost ${VERSION}" -force 2>/dev/null || true
hdiutil detach "/Volumes/Ghost" -force 2>/dev/null || true

attempt=0
max_attempts=4
until hdiutil create \
        -volname "Ghost ${VERSION}" \
        -srcfolder "${STAGING}" \
        -ov -format UDZO \
        "${DMG_OUT}"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "[mac-build] hdiutil create failed after ${max_attempts} attempts"
        exit 1
    fi
    wait_s=$((attempt * 5))
    echo "[mac-build] hdiutil create failed (attempt $attempt/$max_attempts) — waiting ${wait_s}s..."
    # Detach again and wait.
    hdiutil detach "/Volumes/Ghost ${VERSION}" -force 2>/dev/null || true
    rm -f "${DMG_OUT}"
    sleep "$wait_s"
done

echo "[mac-build] dmg: ${DMG_OUT} ($(du -h "${DMG_OUT}" | cut -f1))"
echo "[mac-build] done."
