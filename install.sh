#!/bin/bash

set -e

echo "--- Termux AppStore Installer ---"

pkg update
pkg install -y python python-tkinter p7zip xdg-utils git

pip install -r requirements.txt

APP_DIR="/data/data/com.termux/files/home/.local/share/termux-appstore"
mkdir -p "$APP_DIR"
cp appstore.py "$APP_DIR/"
cp icon.png "$APP_DIR/"

BIN_PATH="/data/data/com.termux/files/usr/bin/termux-appstore"
cat <<EOF > "$BIN_PATH"
#!/bin/bash
export DISPLAY=:0
python "$APP_DIR/appstore.py"
EOF
chmod +x "$BIN_PATH"

DESKTOP_DIR="/data/data/com.termux/files/home/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat <<EOF > "$DESKTOP_DIR/termux-appstore.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=AppStore
Comment=Install Open Source Apps for Termux
Exec=termux-appstore
Icon=$APP_DIR/icon.png
Categories=System;Settings;
Terminal=false
EOF

update-desktop-database "$DESKTOP_DIR" || true

echo "--- Installation Complete! ---"
echo "You can now launch 'AppStore' from your application menu."
