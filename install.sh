#!/usr/bin/env bash
# Install Message Box on a Raspberry Pi (64-bit Bookworm or newer).
# Safe to run again after a git pull. It will not overwrite the token file
# or the config once they exist.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this as root: sudo bash install.sh" >&2
  exit 1
fi

umask 022

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/message-box"
VENV="${INSTALL_DIR}/venv"
ENV_FILE="/etc/message-box/env"
CONFIG_FILE="/etc/message-box/config.toml"
DATA_DIR="/var/lib/message-box"

echo "Installing apt packages..."
apt-get update
apt-get install -y \
  ffmpeg \
  alsa-utils \
  python3-venv \
  python3-pip \
  python3-lgpio \
  ca-certificates \
  espeak-ng \
  i2c-tools

echo "Enabling I2C for the PN532..."
# raspi-config: 0 enables I2C, 1 disables it.
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_i2c 0 || echo "Could not enable I2C with raspi-config."
fi
boot_cfg=""
if [[ -f /boot/firmware/config.txt ]]; then
  boot_cfg="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  boot_cfg="/boot/config.txt"
fi
I2C_BAUD_REBOOT=0
if [[ -n "${boot_cfg}" ]]; then
  if ! grep -Eq '^[[:space:]]*dtparam=i2c_arm=on' "${boot_cfg}"; then
    printf '\n# Message Box NFC reader\ndtparam=i2c_arm=on\n' >> "${boot_cfg}"
    echo "Added dtparam=i2c_arm=on to ${boot_cfg}."
    echo "Reboot if 'i2cdetect -y 1' does not show 24."
    I2C_BAUD_REBOOT=1
  fi
  # The last uncommented baudrate line wins. 10000 Hz lets the PN532
  # clock-stretch without the Pi aborting the transfer (errno 121).
  # Match 10000 exactly: 100000 starts with the same digits and is not slow enough.
  last_baud="$(grep -E '^[[:space:]]*dtparam=i2c_arm_baudrate=' "${boot_cfg}" | tail -n 1 || true)"
  baud_value="${last_baud##*baudrate=}"
  baud_value="${baud_value%%[^0-9]*}"
  if [[ "${baud_value}" != "10000" ]]; then
    printf '\n# PN532 clock stretching. Used only after reboot.\ndtparam=i2c_arm_baudrate=10000\n' >> "${boot_cfg}"
    echo "Set the I2C clock to 10000 Hz in ${boot_cfg}."
    I2C_BAUD_REBOOT=1
  fi
fi
modprobe i2c-dev 2>/dev/null || true
# The systemd unit lists SupplementaryGroups=i2c. systemd will not start
# the service if that group does not exist.
if ! getent group i2c >/dev/null; then
  groupadd --system i2c || true
fi

echo "Copying the program to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
if [[ "${SOURCE}" != "${INSTALL_DIR}" ]]; then
  tar -C "${SOURCE}" \
    --exclude .git \
    --exclude .venv \
    --exclude venv \
    --exclude data \
    --exclude __pycache__ \
    --exclude .pytest_cache \
    --exclude .env \
    --exclude config.toml \
    -cf - . | tar -C "${INSTALL_DIR}" -xf -
fi

echo "Creating the Python virtual environment..."
python3 -m venv --system-site-packages "${VENV}"
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install "${INSTALL_DIR}"

if ! "${VENV}/bin/python" -c "import lgpio" >/dev/null 2>&1; then
  arch="$(uname -m)"
  if [[ "${arch}" == "aarch64" || "${arch}" == "armv7l" ]]; then
    echo "The apt lgpio module was not visible. Trying pip..."
    "${VENV}/bin/pip" install "lgpio>=0.2.2.0"
  else
    echo "Note: lgpio is not installed. That is normal on a laptop; GPIO works on the Pi."
  fi
fi

if ! id messagebox >/dev/null 2>&1; then
  echo "Creating the messagebox user..."
  useradd \
    --system \
    --create-home \
    --home-dir "${DATA_DIR}" \
    --shell /usr/sbin/nologin \
    messagebox
fi

for group in audio gpio plugdev i2c; do
  if getent group "${group}" >/dev/null; then
    usermod -a -G "${group}" messagebox
  else
    echo "Group ${group} does not exist on this machine; skipping it."
  fi
done

mkdir -p /etc/message-box
if [[ ! -f "${CONFIG_FILE}" ]]; then
  cp "${INSTALL_DIR}/config.example.toml" "${CONFIG_FILE}"
  chmod 644 "${CONFIG_FILE}"
  echo "Wrote ${CONFIG_FILE}"
else
  echo "Left existing ${CONFIG_FILE} in place."
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  printf '%s\n' \
    '# Bot token from @BotFather. One line, no quotes.' \
    'TELEGRAM_BOT_TOKEN=' \
    'LG_WD=/var/lib/message-box' > "${ENV_FILE}"
  echo "Wrote ${ENV_FILE}"
fi
chown root:root "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
# lgpio writes a notify pipe in LG_WD. Add it on upgrade without touching the token.
if ! grep -Eq '^[[:space:]]*LG_WD=' "${ENV_FILE}"; then
  printf '%s\n' 'LG_WD=/var/lib/message-box' >> "${ENV_FILE}"
  echo "Added LG_WD to ${ENV_FILE}"
fi

mkdir -p "${DATA_DIR}"
chown -R messagebox:messagebox "${DATA_DIR}"
chmod 700 "${DATA_DIR}"

cp "${INSTALL_DIR}/deploy/message-box.service" /etc/systemd/system/message-box.service
systemctl daemon-reload
systemctl enable message-box.service

echo
if grep -Eq '^[[:space:]]*TELEGRAM_BOT_TOKEN=.+$' "${ENV_FILE}"; then
  systemctl restart message-box.service
  echo "Service restarted."
  echo "Logs: journalctl -u message-box -f"
else
  echo "The token is still empty, so the service was enabled but not started."
  echo "1. Edit ${ENV_FILE} and set TELEGRAM_BOT_TOKEN."
  echo "2. Edit ${CONFIG_FILE} and set allowed_chat_ids (see the README)."
  echo "3. sudo systemctl restart message-box"
fi
echo
echo "Bench test the mic and speaker with:"
echo "  sudo -u messagebox ${VENV}/bin/python -m messagebox --check-audio"
echo "Read an NFC card UID with the service stopped:"
echo "  sudo systemctl stop message-box"
echo "  sudo -u messagebox ${VENV}/bin/python -m messagebox --scan-card"
if [[ "${I2C_BAUD_REBOOT}" == "1" ]]; then
  echo
  echo "A reboot is required before the PN532 uses the new I2C clock:"
  echo "  sudo reboot"
else
  live_hz=""
  freq_node="/sys/class/i2c-adapter/i2c-1/of_node/clock-frequency"
  if [[ -r "${freq_node}" ]]; then
    live_hz="$(python3 -c 'import pathlib,sys; raw=pathlib.Path(sys.argv[1]).read_bytes(); print(int.from_bytes(raw[:4], "big") if len(raw) >= 4 else "")' "${freq_node}" 2>/dev/null || true)"
  fi
  if [[ -n "${live_hz}" && "${live_hz}" != "10000" ]]; then
    echo
    echo "The boot config requests 10000 Hz, but i2c-1 is still running at ${live_hz} Hz."
    echo "A reboot is required: sudo reboot"
  elif [[ "${live_hz}" == "10000" ]]; then
    echo "I2C clock is already 10000 Hz. No reboot is required for the reader."
  fi
fi
