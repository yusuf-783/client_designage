#!/usr/bin/env bash
# ==============================================================================
# Digital Signage Client Updater
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
    log_error "This script must be run as root. Please execute: sudo ./update.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_SRC_DIR="${SCRIPT_DIR}"
OPT_DIR="/opt/digitalsignage"
VENV_DIR="${OPT_DIR}/venv"
SIGNAGE_USER="digitalsignage"
SIGNAGE_GROUP="digitalsignage"

echo -e "\n${BOLD}${CYAN}======================================================================${NC}"
echo -e "${BOLD}${CYAN}   Digital Signage Client Updater                                     ${NC}"
echo -e "${BOLD}${CYAN}======================================================================${NC}\n"

if [ ! -d "${OPT_DIR}/client" ] || [ ! -d "${VENV_DIR}" ]; then
    log_error "Digital Signage Client is not installed at ${OPT_DIR}."
    log_info "Please run: sudo ./install.sh first."
    exit 1
fi

log_info "Updating application files in ${OPT_DIR}/client..."
if [ -d "${CLIENT_SRC_DIR}" ]; then
    rsync -av --exclude "__pycache__" --exclude "*.pyc" --exclude ".venv" "${CLIENT_SRC_DIR}/" "${OPT_DIR}/client/"
    log_success "Application files synchronized."
fi

log_info "Upgrading Python dependencies in virtual environment..."
"${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
if [ -f "${OPT_DIR}/client/requirements.txt" ]; then
    "${VENV_DIR}/bin/pip" install -r "${OPT_DIR}/client/requirements.txt"
    log_success "Dependencies updated."
fi

chown -R "${SIGNAGE_USER}:${SIGNAGE_GROUP}" "${OPT_DIR}"

log_info "Restarting digitalsignage.service..."
if systemctl is-active --quiet digitalsignage.service 2>/dev/null; then
    systemctl restart digitalsignage.service
    log_success "Service restarted successfully."
else
    systemctl start digitalsignage.service || log_warn "Could not start service directly."
fi

echo -e "\n${BOLD}${GREEN}======================================================================${NC}"
echo -e "${BOLD}${GREEN}   Digital Signage Client Updated Successfully!                       ${NC}"
echo -e "${BOLD}${GREEN}======================================================================${NC}\n"
echo -e "Run diagnostic to verify system health: ${CYAN}sudo ./diagnostic.sh${NC}\n"
