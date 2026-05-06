#!/bin/bash
# Deploy Scanline to the Pi.
#
# Usage:
#   ./deploy.sh           — sync code only
#   ./deploy.sh --install — sync + install all system packages (first deploy)
#   ./deploy.sh --full    — same as --install
#
# Prerequisites:
#   SSH key installed for chives@192.168.1.43

set -e

HOST="chives@192.168.1.43"
REMOTE_DIR="/home/chives/scanline"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

DO_INSTALL=0
DO_COMPILE=0

for arg in "$@"; do
    case "$arg" in
        --install|--full) DO_INSTALL=1; DO_COMPILE=1 ;;
        --c|--compile)    DO_COMPILE=1 ;;
        --help|-h)
            sed -n '2,9p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $arg  (try --help)"; exit 1 ;;
    esac
done

echo "==> Syncing code to $HOST:$REMOTE_DIR"
rsync -av --exclude='__pycache__' --exclude='*.pyc' --exclude='.claude' \
    --exclude='state.json' --exclude='*.so' --exclude='.git' \
    "$SCRIPT_DIR/" "$HOST:$REMOTE_DIR/"

if [ "$DO_INSTALL" -eq 1 ]; then
    echo "==> Installing system packages on Pi"
    ssh "$HOST" "sudo apt-get update -qq && sudo apt-get install -y \
        python3-pygame python3-numpy python3-evdev python3-yaml \
        python3-requests xserver-xorg xinit openbox x11-xserver-utils \
        gcc build-essential console-data"
    echo "==> Packages installed"
fi

if [ "$DO_COMPILE" -eq 1 ]; then
    echo "==> Compiling C extension on Pi"
    ssh "$HOST" "cd $REMOTE_DIR/topo && bash build_noise.sh"
    echo "==> C extension compiled"
fi

echo ""
echo "Done. To run on the Pi:"
echo "  ssh chives@192.168.1.43"
echo "  xinit ~/scanline/launch.sh -- :0 vt1 -nolisten tcp"
echo ""
echo "Control via FIFO (from a second SSH session):"
echo "  echo 'next'      | sudo tee /tmp/scanline-ctl"
echo "  echo 'channel 2' | sudo tee /tmp/scanline-ctl"
echo "  echo 'quit'      | sudo tee /tmp/scanline-ctl"
echo ""
echo "First deploy / C extension:"
echo "  ./deploy.sh --install   sync + apt install all packages + compile C ext"
echo "  ./deploy.sh --c         sync + compile C extension (topo/topo_noise.so)"
