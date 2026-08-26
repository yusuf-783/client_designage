#!/usr/bin/env bash
# ==============================================================================
# Digital Signage Client Uninstaller
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

if [ "$EUID" -ne 0 ]; then
    log_error "This script must be run as root. Please execute: sudo ./uninstall.sh"
    exit 1
fi

PURGE_MODE=false
for arg in "$@"; do
    if [ "$arg" == "--purge" ]; then
        PURGE_MODE=true
    fi
done

echo -e "\n${BOLD}${CYAN}======================================================================${NC}"
echo -e "${BOLD}${CYAN}   Digital Signage Client Uninstaller                                 ${NC}"
echo -e "${BOLD}${CYAN}======================================================================${NC}\n"

log_info "Stopping and disabling digitalsignage.service..."
if systemctl is-active --quiet digitalsignage.service 2>/dev/null; then
    systemctl stop digitalsignage.service || true
fi
if systemctl is-enabled --quiet digitalsignage.service 2>/dev/null; then
    systemctl disable digitalsignage.service || true
fi

if [ -f /etc/systemd/system/digitalsignage.service ]; then
    rm -f /etc/systemd/system/digitalsignage.service
    systemctl daemon-reload
    log_success "Removed systemd service unit."
fi

log_info "Removing display and kiosk configuration overrides..."
rm -f /etc/lightdm/lightdm.conf.d/99-disable-blanking.conf
rm -f /etc/X11/xorg.conf.d/10-blanking.conf
rm -f /etc/xdg/autostart/unclutter-kiosk.desktop

log_info "Removing application installation from /opt/digitalsignage..."
rm -rf /opt/digitalsignage

if [ "$PURGE_MODE" = true ]; then
    log_warn "Purge mode enabled: Removing configuration and media cache database..."
    rm -rf /etc/digitalsignage
    rm -rf /var/lib/digitalsignage

    if id -u digitalsignage &>/dev/null; then
        userdel -r digitalsignage 2>/dev/null || userdel digitalsignage 2>/dev/null || true
        log_success "Deleted 'digitalsignage' system user."
    fi
    log_success "All configuration, databases, and media caches purged completely."
else
    log_info "Preserved /etc/digitalsignage and /var/lib/digitalsignage."
    log_info "To completely remove all media caches and database, run: sudo ./uninstall.sh --purge"
fi

echo -e "\n${BOLD}${GREEN}======================================================================${NC}"
echo -e "${BOLD}${GREEN}   Digital Signage Client Uninstalled Successfully!                   ${NC}"
echo -e "${BOLD}${GREEN}======================================================================${NC}\n"
