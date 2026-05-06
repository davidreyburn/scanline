#!/bin/bash
# launch.sh — X11 session starter for Scanline
# Invoked by: xinit /home/chives/scanline/launch.sh -- :0 vt1 -nolisten tcp
export DISPLAY=:0

# Window manager — needed for Chromium in Phase 5, harmless to start now
openbox &

# Give WM a moment to settle
sleep 0.3

exec python3 /home/chives/scanline/scanline.py
