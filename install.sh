#!/usr/bin/env bash
# install.sh — One-shot setup for Scanline on Raspberry Pi OS Bookworm
# Run as root: sudo bash install.sh
set -euo pipefail

SCANLINE_USER=chives
SCANLINE_DIR=/home/${SCANLINE_USER}/scanline
YTDLP_BIN=/usr/local/bin/yt-dlp

echo "=== Scanline install ==="

# --- Swap (Chromium needs headroom on 1 GB Pi 3) --------------------------
if ! grep -q '/swapfile' /etc/fstab; then
    echo "  [swap] creating 512 MB swapfile..."
    fallocate -l 512M /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
else
    echo "  [swap] already configured"
fi

# --- APT dependencies -------------------------------------------------------
echo "  [apt] installing packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    mpv \
    chromium \
    xserver-xorg-legacy \
    xinit \
    openbox \
    python3 \
    python3-pip \
    python3-pygame \
    python3-yaml \
    python3-numpy \
    python3-requests \
    console-data \
    evdev \
    gcc \
    make

# --- Xwrapper: allow any user to run X -------------------------------------
echo "  [xwrap] setting allowed_users=anybody"
cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF

# --- User groups ------------------------------------------------------------
echo "  [groups] adding ${SCANLINE_USER} to input, video, audio"
usermod -aG input,video,audio "${SCANLINE_USER}"

# --- yt-dlp -----------------------------------------------------------------
echo "  [yt-dlp] installing from GitHub releases..."
curl -sSL \
    "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux_armv7l" \
    -o "${YTDLP_BIN}"
chmod +x "${YTDLP_BIN}"
echo "  [yt-dlp] $(${YTDLP_BIN} --version)"

# --- Python packages that aren't in apt ------------------------------------
# (pygame, pyyaml, numpy, requests should all be covered by apt above,
# but pip-install any that might be missing in a venv-less setup)
python3 -m pip install --break-system-packages --quiet \
    requests pyyaml 2>/dev/null || true

# --- Compile topo C extension -----------------------------------------------
if [ -d "${SCANLINE_DIR}/topo" ]; then
    echo "  [topo] compiling noise extension..."
    pushd "${SCANLINE_DIR}/topo" > /dev/null
    bash build_noise.sh
    popd > /dev/null
else
    echo "  [topo] scanline not yet deployed — skipping compile"
fi

# --- systemd service --------------------------------------------------------
SERVICE_SRC="${SCANLINE_DIR}/scanline.service"
SERVICE_DEST=/etc/systemd/system/scanline.service

if [ -f "${SERVICE_SRC}" ]; then
    echo "  [systemd] installing scanline.service..."
    cp "${SERVICE_SRC}" "${SERVICE_DEST}"
    systemctl daemon-reload
    systemctl enable scanline
    echo "  [systemd] enabled (start with: systemctl start scanline)"
else
    echo "  [systemd] scanline.service not found at ${SERVICE_SRC} — skipping"
fi

echo ""
echo "=== Install complete ==="
echo "Next steps:"
echo "  1. Deploy scanline files to ${SCANLINE_DIR}/"
echo "  2. Edit ${SCANLINE_DIR}/channels.yaml (Plex token etc.)"
echo "  3. sudo systemctl start scanline"
echo "  4. sudo journalctl -u scanline -f"
