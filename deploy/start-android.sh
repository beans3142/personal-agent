#!/bin/bash
# Starts Android (Waydroid) without a screen. Runs as the login user.
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export WAYLAND_DISPLAY=wayland-1
pgrep -x weston >/dev/null || nohup weston --backend=headless-backend.so --width=1600 --height=1000 --socket="$WAYLAND_DISPLAY" --idle-time=0 >/tmp/weston.log 2>&1 &
sleep 3
sudo systemctl start waydroid-container
pgrep -f "waydroid session" >/dev/null || nohup waydroid session start >/tmp/waydroid-session.log 2>&1 &
for _ in $(seq 1 60); do
    if [ "$(sudo waydroid shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
        echo booted
        exit 0
    fi
    sleep 5
done
echo "boot timed out"
exit 1
