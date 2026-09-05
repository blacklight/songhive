#!/usr/bin/env bash

# Install Songhive as a systemd service.
#
# Run as root to install a system-wide service under /etc/systemd/system,
# or as a normal user to install a user service under ~/.config/systemd/user.

set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Optional overrides
SONGHIVE_USER="${SONGHIVE_USER:-songhive}"

# Common binaries
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Error: $PYTHON not found." >&2
    exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
    INSTALL_TYPE="system"
else
    INSTALL_TYPE="user"
fi

if [ "$INSTALL_TYPE" = "system" ]; then
    VENV_DIR="/opt/songhive"
    CONFIG_DIR="/etc/songhive"
    DATA_DIR="/var/lib/songhive"
    CACHE_DIR="/var/cache/songhive"
    STATE_DIR="$DATA_DIR"
    LOG_DIR="/var/log/songhive"
    SYSTEMD_DIR="/etc/systemd/system"
    WANTED_BY="multi-user.target"

    # Create a dedicated system user if it does not already exist.
    if ! id -u "$SONGHIVE_USER" >/dev/null 2>&1; then
        if ! getent group "$SONGHIVE_USER" >/dev/null 2>&1; then
            groupadd -r "$SONGHIVE_USER"
        fi
        useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" -g "$SONGHIVE_USER" -M "$SONGHIVE_USER"
        echo "Created system user $SONGHIVE_USER"
    fi

    SERVICE_USER="$SONGHIVE_USER"
    SERVICE_GROUP="$SONGHIVE_USER"
else
    VENV_DIR="${VENV_DIR:-$HOME/.local/share/virtualenvs/songhive}"
    CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/songhive"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/songhive"
    CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/songhive"
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/songhive"
    LOG_DIR="$STATE_DIR/log"
    SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    WANTED_BY="default.target"
    SERVICE_USER="%u"
    SERVICE_GROUP="%g"
fi

SCHEDULE_FILE="$STATE_DIR/celerybeat-schedule"

echo "Installing Songhive as a $INSTALL_TYPE service..."
echo "  Virtual env:    $VENV_DIR"
echo "  Config dir:     $CONFIG_DIR"
echo "  Data dir:       $DATA_DIR"
echo "  Cache dir:      $CACHE_DIR"
echo "  State dir:      $STATE_DIR"
echo "  Log dir:        $LOG_DIR"
echo "  Systemd dir:    $SYSTEMD_DIR"
echo "  Schedule file:  $SCHEDULE_FILE"

# Create directories
mkdir -p "$VENV_DIR" "$CONFIG_DIR" "$DATA_DIR" "$CACHE_DIR" "$STATE_DIR" "$LOG_DIR" "$SYSTEMD_DIR"

# Create and populate the virtual environment
if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Creating Python virtual environment at $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$REPO_ROOT"

if [ "$INSTALL_TYPE" = "system" ]; then
    chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR" "$CACHE_DIR" "$STATE_DIR" "$LOG_DIR"
    # The venv is owned by root but readable/executable by the service user.
    chmod -R o+rX "$VENV_DIR"
fi

# Copy example config and remind the user to edit it
cp "$REPO_ROOT/config.toml.example" "$CONFIG_DIR/config.toml.example"
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    echo
    echo "Example configuration copied to $CONFIG_DIR/config.toml.example"
    echo "Please copy it to $CONFIG_DIR/config.toml and edit it before starting the app:"
    echo
    echo "  cp $CONFIG_DIR/config.toml.example $CONFIG_DIR/config.toml"
    echo
else
    echo
    echo "Existing configuration found at $CONFIG_DIR/config.toml"
fi

if [ "$INSTALL_TYPE" = "user" ]; then
    echo "For a non-root install, make sure to set storage.local_path to a writable directory, e.g.:"
    echo "  storage.local_path = \"$DATA_DIR/media\""
    echo
fi

# Optional frontend build
if [ -d "$REPO_ROOT/frontend" ] && command -v npm >/dev/null 2>&1; then
    echo "Building frontend..."
    (cd "$REPO_ROOT/frontend" && npm install && npm run build) || echo "Frontend build skipped or failed; run it manually if you need the web UI."
else
    echo "Frontend build skipped (no frontend/ directory or npm not found)."
fi

# Install systemd units
UNITS="songhive.service songhive-server.service songhive-celery.service songhive-watch-extlib.service"
for unit in $UNITS; do
    src="$REPO_ROOT/config/systemd/$unit"
    dst="$SYSTEMD_DIR/$unit"
    sed \
        -e "s|@@VENV_DIR@@|$VENV_DIR|g" \
        -e "s|@@CONFIG_DIR@@|$CONFIG_DIR|g" \
        -e "s|@@DATA_DIR@@|$DATA_DIR|g" \
        -e "s|@@CACHE_DIR@@|$CACHE_DIR|g" \
        -e "s|@@LOG_DIR@@|$LOG_DIR|g" \
        -e "s|@@SCHEDULE_FILE@@|$SCHEDULE_FILE|g" \
        -e "s|@@USER@@|$SERVICE_USER|g" \
        -e "s|@@GROUP@@|$SERVICE_GROUP|g" \
        -e "s|@@WANTED_BY@@|$WANTED_BY|g" \
        "$src" > "$dst"
    echo "Installed $dst"
done

# Reload systemd and report next steps
echo
if [ "$INSTALL_TYPE" = "system" ]; then
    systemctl daemon-reload
    echo
    echo "Songhive systemd units installed. Start with:"
    echo "  systemctl start songhive.service"
    echo
    echo "Enable on boot with:"
    echo "  systemctl enable songhive.service"
else
    if systemctl --user daemon-reload 2>/dev/null; then
        echo "Songhive user systemd units installed. Start with:"
        echo "  systemctl --user start songhive.service"
        echo
        echo "Enable on login with:"
        echo "  systemctl --user enable songhive.service"
    else
        echo "Songhive user systemd units installed to $SYSTEMD_DIR."
        echo "Reload manually and then start with:"
        echo "  systemctl --user daemon-reload"
        echo "  systemctl --user start songhive.service"
        echo
        echo "Enable on login with:"
        echo "  systemctl --user enable songhive.service"
    fi
fi

echo
echo "Remember to configure $CONFIG_DIR/config.toml before starting the service."
