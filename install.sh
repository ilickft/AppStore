#!/bin/bash

# Termux AppStore Installer
# Targeted for XFCE4 Desktop Environment in Termux

set -e

echo "--- Termux AppStore Installer ---"

# 1. Install System Dependencies
echo "Installing system dependencies..."
pkg update
pkg install -y python python-tkinter p7zip xdg-utils git

# 2. Install Python Dependencies
echo "Installing python dependencies..."
pip install customtkinter requests pillow pystray python-xlib

# 3. Setup Application Directory
APP_DIR="/data/data/com.termux/files/home/.local/share/termux-appstore"
mkdir -p "$APP_DIR"
cp appstore.py "$APP_DIR/"
cp AppStore.png "$APP_DIR/"

# 4. Create Binary Wrapper
echo "Creating binary wrapper..."
BIN_PATH="/data/data/com.termux/files/usr/bin/termux-appstore"
cat <<EOF > "$BIN_PATH"
#!/bin/bash
export DISPLAY=:0
python "$APP_DIR/appstore.py"
EOF
chmod +x "$BIN_PATH"

# 5. Create Desktop Entry
echo "Creating desktop entry..."
DESKTOP_DIR="/data/data/com.termux/files/home/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat <<EOF > "$DESKTOP_DIR/termux-appstore.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=AppStore
Comment=Install Open Source Apps for Termux
Exec=termux-appstore
Icon=$APP_DIR/AppStore.png
Categories=System;Settings;
Terminal=false
EOF

# 6. Update Desktop Database
echo "Updating desktop database..."
update-desktop-database "$DESKTOP_DIR" || true

echo "--- Installation Complete! ---"
echo "You can now launch 'AppStore' from your application menu."
