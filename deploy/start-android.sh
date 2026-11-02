#!/bin/bash
# Starts Android (Waydroid) without a screen, as user services that outlive the login.
set -u
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemd-run --user --unit=android-weston --collect \
    weston --backend=headless-backend.so --width=1600 --height=1000 --socket=wayland-1 --idle-time=0 >/dev/null 2>&1
sleep 3
sudo systemctl restart waydroid-container
sleep 3
systemd-run --user --unit=android-session --collect --setenv=WAYLAND_DISPLAY=wayland-1 --setenv=XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    waydroid session start >/dev/null 2>&1
for _ in $(seq 1 40); do
    sleep 10
    if [ "$(sudo waydroid shell -- getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
        # A screen that turns off freezes the container, so keep it on.
        sudo waydroid shell -- svc power stayon true
        sudo waydroid shell -- settings put global stay_on_while_plugged_in 7
        sudo waydroid shell -- settings put system screen_off_timeout 2147483647
        echo booted
        exit 0
    fi
done
echo "boot timed out"
exit 1
