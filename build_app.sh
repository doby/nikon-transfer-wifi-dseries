#!/usr/bin/env bash
# Build "Nikon Transfer.app" — a standalone double-clickable macOS app.
#
# Usage:
#   ./build_app.sh           # build into ./dist/Nikon Transfer.app
#   ./build_app.sh --open    # build and reveal the app in Finder
#   ./build_app.sh --install # build, then copy the .app to /Applications

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "pyinstaller absent — installation : pip install '.[build]'" >&2
    exit 1
fi

echo "→ Nettoyage des builds précédents…"
# chflags : le drapeau d'immutabilité peut empêcher rm -rf de nettoyer.
chflags -R nouchg build dist 2>/dev/null || true
rm -rf build dist

echo "→ Construction du bundle…"
pyinstaller nikon_transfer.spec --noconfirm --log-level=WARN

APP="dist/Nikon Transfer.app"
if [[ ! -d "$APP" ]]; then
    echo "❌ Échec : $APP introuvable" >&2
    exit 1
fi

# Strip macOS quarantine so double-click ne déclenche pas Gatekeeper.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

SIZE=$(du -sh "$APP" | cut -f1)
echo "✓ $APP ($SIZE)"

case "${1:-}" in
    --open)
        open -R "$APP"
        ;;
    --install)
        DEST="/Applications/Nikon Transfer.app"
        echo "→ Copie vers $DEST…"
        rm -rf "$DEST"
        cp -R "$APP" "$DEST"
        echo "✓ Installé dans /Applications"
        open -R "$DEST"
        ;;
esac
