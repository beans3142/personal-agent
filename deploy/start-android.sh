#!/bin/bash
# Starts Android (Waydroid) without a screen, as user services that outlive the login.
set -euo pipefail
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
if ! systemctl --user is-active --quiet android-weston; then
    systemd-run --user --unit=android-weston --collect \
        weston --backend=headless-backend.so --width=1600 --height=1000 --socket=wayland-1 --idle-time=0 >/dev/null 2>&1
fi
sleep 3
sudo systemctl start waydroid-container
sleep 3
if ! systemctl --user is-active --quiet android-session; then
    systemd-run --user --unit=android-session --collect --setenv=WAYLAND_DISPLAY=wayland-1 --setenv=XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        waydroid session start >/dev/null 2>&1
fi
for _ in $(seq 1 40); do
    sleep 10
    if [ "$(sudo timeout 8 waydroid shell -- getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)" = "1" ]; then
        # A screen that turns off freezes the container, so keep it on.
        sudo waydroid shell -- svc power stayon true
        sudo waydroid shell -- settings put global stay_on_while_plugged_in 7
        sudo waydroid shell -- settings put system screen_off_timeout 2147483647
        for scale in window_animation_scale transition_animation_scale animator_duration_scale; do
            sudo waydroid shell -- settings put global "$scale" 0
        done
        echo booted
        exit 0
    fi
done
echo "boot timed out"
exit 1
