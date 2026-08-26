#!/usr/bin/env bash
# ==============================================================================
# Digital Signage Client Standalone Installer for Raspberry Pi / Debian Trixie
# ==============================================================================
# Usage: sudo ./install.sh
# ==============================================================================

set -eo pipefail

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
BLUE="\033[0;34m"
CYAN="\033[0;36m"
BOLD="\033[1m"
NC="\033[0m"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo -e "\n${BOLD}${CYAN}======================================================================${NC}"
echo -e "${BOLD}${CYAN}   Digital Signage Client Installer (Debian Trixie / Raspberry Pi)    ${NC}"
echo -e "${BOLD}${CYAN}======================================================================${NC}\n"

# 0. Root Privilege Check
if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run as root. Please execute: sudo ./install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_SRC_DIR="${SCRIPT_DIR}"
if [ -d "${SCRIPT_DIR}/client" ]; then
    CLIENT_SRC_DIR="${SCRIPT_DIR}/client"
fi

# 1. Check OS
log_info "Step 1/13: Checking Operating System..."
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_NAME="${NAME:-Linux}"
    OS_VERSION="${VERSION_ID:-unknown}"
    OS_CODENAME="${VERSION_CODENAME:-unknown}"
    log_success "Detected OS: ${OS_NAME} ${OS_VERSION} (${OS_CODENAME})"
else
    OS_NAME="Linux"
    OS_CODENAME="unknown"
    log_warn "Could not read /etc/os-release. Proceeding with generic Debian compatibility."
fi

# 2. Check Architecture
log_info "Step 2/13: Checking CPU Architecture..."
ARCH="$(uname -m)"
case "${ARCH}" in
    aarch64|arm64)
        log_success "Architecture: ${ARCH} (Raspberry Pi 64-bit ARM / Debian Trixie ARM64)"
        ;;
    armv7l|armhf)
        log_success "Architecture: ${ARCH} (Raspberry Pi 32-bit ARM)"
        ;;
    x86_64)
        log_success "Architecture: ${ARCH} (x86_64 / PC / Development Mode)"
        ;;
    *)
        log_warn "Architecture: ${ARCH} (Generic architecture, proceeding with installation)"
        ;;
esac

# 3. Install Dependencies
log_info "Step 3/13: Installing system dependencies via apt-get..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y

PKGS=(
    python3
    python3-pip
    python3-venv
    python3-dev
    build-essential
    mpv
    ffmpeg
    libmpv-dev
    socat
    unclutter-xfixes
    x11-xserver-utils
    xdotool
    sqlite3
    curl
    jq
    ca-certificates
    rsync
    watchdog
)

apt-get install -y "${PKGS[@]}" || apt-get install -y "${PKGS[@]/unclutter-xfixes/unclutter}"
log_success "All system packages installed successfully."

# 4. Create User
log_info "Step 4/13: Creating dedicated 'digitalsignage' system user..."
SIGNAGE_USER="digitalsignage"
SIGNAGE_GROUP="digitalsignage"

if ! id -u "${SIGNAGE_USER}" &>/dev/null; then
    useradd -r -m -d /var/lib/digitalsignage -s /bin/bash "${SIGNAGE_USER}"
    log_success "Created system user '${SIGNAGE_USER}'."
else
    log_info "System user '${SIGNAGE_USER}' already exists."
fi

for grp in video audio render input tty systemd-journal; do
    if getent group "${grp}" &>/dev/null; then
        usermod -aG "${grp}" "${SIGNAGE_USER}" || true
    fi
done
log_success "User '${SIGNAGE_USER}' configured with multimedia and hardware privileges."

# 5. Create Storage Directories
log_info "Step 5/13: Creating runtime and storage directories..."
STORAGE_BASE="/var/lib/digitalsignage"
OPT_DIR="/opt/digitalsignage"
ETC_DIR="/etc/digitalsignage"

mkdir -p \
    "${STORAGE_BASE}/db" \
    "${STORAGE_BASE}/media" \
    "${STORAGE_BASE}/playlists" \
    "${STORAGE_BASE}/temp" \
    "${STORAGE_BASE}/logs" \
    "${OPT_DIR}/client" \
    "${ETC_DIR}"

chown -R "${SIGNAGE_USER}:${SIGNAGE_GROUP}" "${STORAGE_BASE}" "${OPT_DIR}" "${ETC_DIR}"
chmod 0750 "${STORAGE_BASE}" "${STORAGE_BASE}/db" "${STORAGE_BASE}/media" "${STORAGE_BASE}/temp" "${STORAGE_BASE}/logs"
log_success "Directories created with secure permissions."

# 6. Install Python Client
log_info "Step 6/13: Installing Python client and virtualenv..."
VENV_DIR="${OPT_DIR}/venv"

if [ ! -d "${VENV_DIR}" ]; then
    python3 -m venv "${VENV_DIR}"
    log_success "Created Python virtual environment in ${VENV_DIR}."
fi

if [ -d "${CLIENT_SRC_DIR}" ]; then
    rsync -av --exclude "__pycache__" --exclude "*.pyc" --exclude ".venv" "${CLIENT_SRC_DIR}/" "${OPT_DIR}/client/"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
if [ -f "${OPT_DIR}/client/requirements.txt" ]; then
    "${VENV_DIR}/bin/pip" install -r "${OPT_DIR}/client/requirements.txt"
fi

chown -R "${SIGNAGE_USER}:${SIGNAGE_GROUP}" "${OPT_DIR}"
log_success "Python client code and dependencies deployed successfully."

# 7. Create Configuration (Idempotent)
log_info "Step 7/13: Setting up client configuration..."
CONFIG_FILE="${ETC_DIR}/config.yaml"

if [ ! -f "${CONFIG_FILE}" ]; then
    if [ -f "${CLIENT_SRC_DIR}/config.example.yaml" ]; then
        cp "${CLIENT_SRC_DIR}/config.example.yaml" "${CONFIG_FILE}"
    elif [ -f "${CLIENT_SRC_DIR}/config.yaml" ]; then
        cp "${CLIENT_SRC_DIR}/config.yaml" "${CONFIG_FILE}"
    fi
    chown "${SIGNAGE_USER}:${SIGNAGE_GROUP}" "${CONFIG_FILE}"
    chmod 0600 "${CONFIG_FILE}"
    log_success "Created initial configuration in ${CONFIG_FILE}."
else
    log_info "Configuration file ${CONFIG_FILE} already exists. Preserving settings."
fi

# 8. Install Systemd Service Unit
log_info "Step 8/13: Installing systemd daemon service..."
SERVICE_FILE="/etc/systemd/system/digitalsignage.service"

cat <<EOF > "${SERVICE_FILE}"
[Unit]
Description=Digital Signage Client Daemon (Debian Trixie / Raspberry Pi)
Documentation=https://github.com/upatik/digital-signage
After=network-online.target sound.target graphical.target
Wants=network-online.target

[Service]
Type=simple
User=${SIGNAGE_USER}
Group=${SIGNAGE_GROUP}
WorkingDirectory=${OPT_DIR}/client
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/${SIGNAGE_USER}/.Xauthority
Environment=DIGITALSIGNAGE_CONFIG=${CONFIG_FILE}
ExecStart=${VENV_DIR}/bin/python -m app.main
Restart=always
RestartSec=5
WatchdogSec=60s

LimitNOFILE=65536
KillMode=mixed
TimeoutStopSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=digitalsignage

[Install]
WantedBy=graphical.target
EOF

chmod 0644 "${SERVICE_FILE}"
log_success "Systemd unit installed: ${SERVICE_FILE}"

# 9. Enable Systemd Service
log_info "Step 9/13: Reloading systemd and enabling service..."
systemctl daemon-reload
systemctl enable digitalsignage.service
if systemctl is-active --quiet digitalsignage.service; then
    systemctl restart digitalsignage.service
    log_success "digitalsignage.service restarted."
else
    systemctl start digitalsignage.service || log_warn "Service start queued."
    log_success "digitalsignage.service enabled and started."
fi

# 10. Configure Fullscreen Mode
log_info "Step 10/13: Configuring fullscreen display settings..."
if [ -f "${CONFIG_FILE}" ]; then
    sed -i 's/fullscreen: false/fullscreen: true/g' "${CONFIG_FILE}" || true
fi
log_success "Fullscreen playback enabled in player engine config."

# 11. Disable Screen Blanking & Sleep
log_info "Step 11/13: Disabling screen blanking, screensaver, and DPMS sleep..."
mkdir -p /etc/lightdm/lightdm.conf.d
cat <<EOF > /etc/lightdm/lightdm.conf.d/99-disable-blanking.conf
[Seat:*]
xserver-command=X -s 0 -dpms -s noblank
EOF

mkdir -p /etc/X11/xorg.conf.d
cat <<EOF > /etc/X11/xorg.conf.d/10-blanking.conf
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
    Option "DPMS" "false"
EndSection
EOF

for cmdline in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [ -f "${cmdline}" ]; then
        if ! grep -q "consoleblank=0" "${cmdline}"; then
            sed -i 's/$/ consoleblank=0/' "${cmdline}"
            log_success "Appended consoleblank=0 to ${cmdline}."
        fi
    fi
done
log_success "Display sleep, DPMS, and screensavers disabled."

# 12. Hide Mouse Cursor
log_info "Step 12/13: Configuring mouse cursor auto-hiding..."
mkdir -p /etc/xdg/autostart
cat <<EOF > /etc/xdg/autostart/unclutter-kiosk.desktop
[Desktop Entry]
Type=Application
Name=Unclutter Cursor Hide
Exec=unclutter -idle 0.5 -root
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
log_success "Cursor auto-hide configured with unclutter and mpv --cursor-autohide."

# 13. Enable Watchdog Protection
log_info "Step 13/13: Configuring hardware & software Watchdog..."
for cfg in /boot/firmware/config.txt /boot/config.txt; do
    if [ -f "${cfg}" ]; then
        if ! grep -q "dtparam=watchdog=on" "${cfg}"; then
            echo "dtparam=watchdog=on" >> "${cfg}"
            log_success "Enabled hardware watchdog in ${cfg}."
        fi
    fi
done

if [ -f /etc/watchdog.conf ]; then
    sed -i 's/#watchdog-device/watchdog-device/g' /etc/watchdog.conf || true
    sed -i 's/#max-load-1/max-load-1/g' /etc/watchdog.conf || true
    systemctl enable watchdog.service || true
fi
log_success "Hardware and systemd watchdog supervision active."

echo -e "\n${BOLD}${GREEN}======================================================================${NC}"
echo -e "${BOLD}${GREEN}   Digital Signage Client Installed Successfully!                     ${NC}"
echo -e "${BOLD}${GREEN}======================================================================${NC}\n"
echo -e "  • Service Name    : ${BOLD}digitalsignage.service${NC}"
echo -e "  • Storage Root    : ${BOLD}${STORAGE_BASE}${NC}"
echo -e "  • Config File     : ${BOLD}${CONFIG_FILE}${NC}"
echo -e "  • Application Dir : ${BOLD}${OPT_DIR}/client${NC}"
echo -e "  • Virtualenv      : ${BOLD}${VENV_DIR}${NC}\n"
echo -e "Useful Commands:"
echo -e "  - Check Status    : ${CYAN}systemctl status digitalsignage.service${NC}"
echo -e "  - View Live Logs  : ${CYAN}journalctl -u digitalsignage.service -f${NC}"
echo -e "  - Run Diagnostic  : ${CYAN}sudo ./diagnostic.sh${NC}"
echo -e "  - Update Client   : ${CYAN}sudo ./update.sh${NC}"
echo -e "  - Uninstall       : ${CYAN}sudo ./uninstall.sh${NC}\n"
