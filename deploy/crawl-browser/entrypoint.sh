#!/bin/sh
# Start a virtual X display for headful Chromium, then hand PID 1 to the service.
set -eu
export DISPLAY=:99
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
i=0
until [ -e /tmp/.X11-unix/X99 ] || [ $i -ge 50 ]; do i=$((i+1)); sleep 0.1; done
exec "$@"
