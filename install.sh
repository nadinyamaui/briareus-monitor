#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo 'Run this installer as root, with the path to a private JSON configuration file.' >&2
    exit 1
fi
config_source=${1:?Usage: install.sh /absolute/path/to/private-config.json}
source_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null
command -v systemctl >/dev/null
python3 "$source_directory/briareus_monitor.py" --config "$config_source" --check-config
install -d -m 0755 /usr/local/lib/briareus-monitor
install -d -m 0700 /etc/briareus-monitor
install -m 0644 "$source_directory/collector.py" "$source_directory/briareus_monitor.py" /usr/local/lib/briareus-monitor/
if [ "$config_source" != /etc/briareus-monitor/config.json ]; then
    install -m 0600 "$config_source" /etc/briareus-monitor/config.json
fi
install -m 0644 "$source_directory/briareus-monitor.service" /etc/systemd/system/briareus-monitor.service
systemctl daemon-reload
systemctl enable briareus-monitor.service
systemctl restart briareus-monitor.service
systemctl is-active --quiet briareus-monitor.service
echo 'Briareus monitor is running; it sends resources over HTTPS every minute.'
