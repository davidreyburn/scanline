#!/bin/bash
# Build topo_noise.so on the Pi.
# Run once after any change to topo_noise.c:
#   bash /home/chives/scanline/topo/build_noise.sh
set -e
DIR="$(dirname "$(realpath "$0")")"
cd "$DIR"
echo "Building topo_noise.so ..."
gcc -O3 -march=native -shared -fPIC -o topo_noise.so topo_noise.c -lm -lpthread
echo "Done: $(ls -lh topo_noise.so | awk '{print $5, $9}')"
