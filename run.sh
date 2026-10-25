#!/usr/bin/env bash
# HOSHIYOMI LOG を起動。PyQt6 は pip 版(~/.local)なので fcitx プラグインが読めない
# → ネイティブ Wayland + text-input プロトコル(QT_IM_MODULE=wayland)で日本語入力を通す
cd "$(dirname "$(readlink -f "$0")")" || exit 1
export QT_QPA_PLATFORM=wayland   # セッションの xcb を上書き(xcb だと text-input が効かない)
export QT_IM_MODULE=wayland
exec python3 app.py "$@"
