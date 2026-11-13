#!/usr/bin/env bash
# ==========================================================================
#  Hanwha Camera Analytics - installer
#
#  Usage (from inside this folder):   bash install.sh
#
#  What it does:
#    1. Installs Docker if it isn't already installed
#    2. Asks a few questions (press Enter to accept the [default])
#    3. Asks for the passwords to use and writes everything to .env
#    4. Builds and starts all containers
#    5. Prints the Grafana address and the settings to enter on each camera
#
#  Safe to run again: it offers to keep your existing settings.
# ==========================================================================
set -euo pipefail
cd "$(dirname "$0")"

bold=$'\e[1m'; green=$'\e[32m'; yellow=$'\e[33m'; red=$'\e[31m'; reset=$'\e[0m'
info() { echo "${green}==>${reset} $*"; }
warn() { echo "${yellow}!! ${reset} $*"; }
die()  { echo "${red}ERROR:${reset} $*" >&2; exit 1; }

ask() {   # ask "Question" "default" -> echoes the answer
    local reply
    read -r -p "$1 [${2}]: " reply
    echo "${reply:-$2}"
}
ask_yn() { # ask_yn "Question" "Y|N" -> returns 0 for yes
    local reply
    read -r -p "$1 [$2]: " reply
    reply="${reply:-$2}"
    [[ "$reply" =~ ^[Yy] ]]
}
# ask_password "Prompt" [min_length] -> echoes the password
# Typing is hidden, the password is entered twice, and characters that would
# break the settings file (.env) are rejected.
ask_password() {
    local p1 p2 min="${2:-8}"
    while true; do
        read -r -s -p "$1: " p1; echo >&2
        if (( ${#p1} < min )); then
            echo "  ${yellow}Must be at least ${min} characters.${reset}" >&2; continue
        fi
        if [[ "$p1" =~ [[:space:]\$\#\'\"\\\`] ]]; then
            echo "  ${yellow}Please don't use spaces or any of these:  \$ # ' \" \\ \`${reset}" >&2; continue
        fi
        read -r -s -p "  Re-enter to confirm: " p2; echo >&2
        if [[ "$p1" != "$p2" ]]; then
            echo "  ${yellow}Passwords didn't match, try again.${reset}" >&2; continue
        fi
        printf '%s' "$p1"; return
    done
}

# Files copied from Windows can have CRLF line endings, which break shell
# scripts and .env values. Convert every text file to Unix line endings.
fix_line_endings() {
    find . -type f \( -name '*.sh' -o -name '*.yml' -o -name '*.yaml' -o -name '*.py' \
        -o -name '*.sql' -o -name '*.json' -o -name '*.txt' -o -name '.env*' \
        -o -name 'Dockerfile' \) -exec sed -i 's/\r$//' {} +
}

# ---------------------------------------------------------------- 1. Docker
SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"

if ! command -v docker >/dev/null 2>&1; then
    warn "Docker is not installed."
    if ask_yn "Install Docker now (uses the official get.docker.com script)?" "Y"; then
        curl -fsSL https://get.docker.com | $SUDO sh
        [[ -n "$SUDO" ]] && $SUDO usermod -aG docker "$USER" || true
    else
        die "Docker is required. Install it and run this script again."
    fi
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    DOCKER="$SUDO docker"    # user not in docker group yet (first run)
    $DOCKER info >/dev/null 2>&1 || die "Docker is installed but not running. Try: sudo systemctl start docker"
fi
$DOCKER compose version >/dev/null 2>&1 || die "Docker Compose plugin missing. Re-run the Docker install."

fix_line_endings

# ---------------------------------------------------------------- 2. Settings
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
HOST_IP="${HOST_IP:-<this-server-ip>}"

write_env=true
if [[ -f .env ]]; then
    echo
    if ask_yn "Existing settings (.env) found. Keep them?" "Y"; then
        write_env=false
    else
        cp .env ".env.backup.$(date +%Y%m%d-%H%M%S)"
        info "Old settings backed up."
    fi
fi

if $write_env; then
    echo
    echo "${bold}--- MQTT broker ---${reset}"
    echo "This bundle includes its own MQTT broker (Mosquitto)."
    echo "Choose 'N' only if you already have a broker you want to use instead."
    if ask_yn "Use the built-in broker?" "Y"; then
        COMPOSE_PROFILES="broker"
        MQTT_HOST="mosquitto"            # container name on the internal network
        MQTT_PORT="1883"
        BROKER_PUBLIC_PORT="$(ask "Port cameras will connect to" "1883")"
        if ask_yn "Require a username/password for the broker? (recommended)" "Y"; then
            MQTT_USER="$(ask "Broker username" "hanwha")"
            echo "Cameras will use this login to connect to the broker."
            MQTT_PASS="$(ask_password "Broker password")"
        else
            MQTT_USER=""; MQTT_PASS=""
        fi
    else
        COMPOSE_PROFILES=""
        BROKER_PUBLIC_PORT="1883"
        MQTT_HOST="$(ask "Existing broker IP or hostname" "192.168.1.50")"
        MQTT_PORT="$(ask "Existing broker port" "1883")"
        MQTT_USER="$(ask "Broker username (blank if none)" "")"
        MQTT_PASS=""
        if [[ -n "$MQTT_USER" ]]; then MQTT_PASS="$(ask_password "Broker password" 1)"; fi
    fi

    echo
    echo "${bold}--- Cameras ---${reset}"
    echo "Topic filter: which MQTT topics to record. The default records every"
    echo "Hanwha ONVIF event, including all WiseAI analytics."
    MQTT_TOPICS="$(ask "Topic filter" "+/onvif-ej/#")"
    echo
    echo "Optional friendly names, format  prefix=Name,prefix=Name"
    echo "  prefix = the camera's MAC address or its MQTT Default Topic Prefix"
    echo "  e.g. E4:30:22:C5:6F:14=Lobby,TestCam=Front-Door"
    echo "  (You can add these later by editing .env.)"
    CAMERA_NAMES="$(ask "Camera names" "")"

    echo
    echo "${bold}--- Grafana (dashboard website) ---${reset}"
    GRAFANA_PORT="$(ask "Grafana web port" "3000")"
    GRAFANA_ADMIN_USER="$(ask "Grafana admin username" "admin")"
    GRAFANA_ADMIN_PASSWORD="$(ask_password "Grafana admin password")"

    echo
    echo "${bold}--- Database ---${reset}"
    echo "Used only between the containers on this server; you won't need to type it again."
    POSTGRES_PASSWORD="$(ask_password "Database password")"

    TZ_DEFAULT="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)"
    TZ_VALUE="$(ask "Time zone" "$TZ_DEFAULT")"

    umask 077
    cat > .env <<EOF
# Generated by install.sh on $(date)
# Edit values here, then run:  docker compose up -d

# --- MQTT broker ---
# "broker" = run the built-in Mosquitto. Empty = use an existing broker (MQTT_HOST).
COMPOSE_PROFILES=${COMPOSE_PROFILES}
MQTT_HOST=${MQTT_HOST}
MQTT_PORT=${MQTT_PORT}
BROKER_PUBLIC_PORT=${BROKER_PUBLIC_PORT}
MQTT_USER=${MQTT_USER}
MQTT_PASS=${MQTT_PASS}

# --- What to record ---
MQTT_TOPICS=${MQTT_TOPICS}
CAMERA_NAMES=${CAMERA_NAMES}

# --- Database (internal only, not exposed to the network) ---
POSTGRES_DB=camdata
POSTGRES_USER=camdata
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# --- Grafana ---
GRAFANA_PORT=${GRAFANA_PORT}
GRAFANA_ADMIN_USER=${GRAFANA_ADMIN_USER}
GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}

TZ=${TZ_VALUE}
EOF
    umask 022
    info "Settings saved to .env (readable only by you)."
fi

# Load settings for the summary below
set -a; source .env; set +a

# ---------------------------------------------------------------- 3. Start
echo
info "Downloading images and starting containers (first run takes a few minutes)..."
$DOCKER compose up -d --build

# The database and Grafana only read their passwords the FIRST time they start
# (when their data volume is empty). Apply the passwords from .env to them every
# time, so changing a password by re-running this script actually takes effect.
info "Applying passwords..."
for _ in $(seq 1 60); do
    $DOCKER compose exec -T db pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" 2>/dev/null && break
    sleep 2
done
$DOCKER compose exec -T db psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD '$POSTGRES_PASSWORD';" >/dev/null \
    || warn "Could not update the database password (see: docker compose logs db)"

info "Waiting for Grafana to come up..."
for _ in $(seq 1 60); do
    if curl -fs "http://localhost:${GRAFANA_PORT:-3000}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
$DOCKER compose exec -T grafana grafana cli admin reset-admin-password "$GRAFANA_ADMIN_PASSWORD" >/dev/null 2>&1 \
    || warn "Could not update the Grafana admin password (see: docker compose logs grafana)"

echo
$DOCKER compose ps
echo

# ---------------------------------------------------------------- 4. Summary
cat <<EOF
${bold}=====================================================================${reset}
${bold} Installed.${reset}

${bold} Dashboard${reset}
   http://${HOST_IP}:${GRAFANA_PORT:-3000}
   Login: ${GRAFANA_ADMIN_USER} / (the Grafana password you entered)

${bold} Camera settings${reset}  (each camera: Setup > Event > MQTT > Client Setup)
EOF
if [[ "${COMPOSE_PROFILES:-}" == *broker* ]]; then
cat <<EOF
   MQTT:         Enable
   Broker/Host:  ${HOST_IP}
   Port:         ${BROKER_PUBLIC_PORT:-1883}
   Username:     ${MQTT_USER:-(none)}
   Password:     $([[ -n "${MQTT_PASS:-}" ]] && echo "(the broker password you entered)" || echo "(none)")
EOF
else
cat <<EOF
   Point cameras at your existing broker: ${MQTT_HOST}:${MQTT_PORT}
EOF
fi
cat <<EOF

${bold} Useful commands${reset}  (run in this folder)
   docker compose logs -f ingest    watch messages arrive (Ctrl+C to stop watching)
   docker compose ps                check all containers are "Up"
   docker compose restart           restart everything
   bash install.sh                  re-run setup / change settings

 All settings and passwords are stored in .env (readable only by you).
 Forgot a password?  grep PASS .env
${bold}=====================================================================${reset}
EOF

if [[ "$DOCKER" == "sudo docker" ]]; then
    warn "Log out and back in so you can run 'docker' commands without sudo."
fi
