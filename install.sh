#!/bin/sh
# Add Label Printer to the application menu of the current user.
# Usage: ./install.sh            install
#        ./install.sh --remove   uninstall
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
entry="$applications/io.github.gnoasti.LabelPrinter.desktop"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$entry"
    echo "Removed $entry"
    exit 0
fi

mkdir -p "$applications"
cat > "$entry" <<EOF
[Desktop Entry]
Type=Application
Name=Label Printer
Comment=Print text labels on a Brother QL-700
Exec="$here/label-printer"
Icon=printer
Terminal=false
Categories=Utility;Office;
Keywords=label;brother;ql-700;sticker;
StartupWMClass=io.github.gnoasti.LabelPrinter
EOF

echo "Installed $entry"
echo "The entry points to this folder; run this script again if you move it."
