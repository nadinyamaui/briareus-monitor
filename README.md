# Briareus Monitor

A small **Linux resource monitoring service**, separate from Briareus and from
AI agents. It reads CPU, memory, local disks and network counters and sends them
**outbound over HTTPS once a minute**. Briareus does not open recurring SSH
connections. SSH is only needed to install, update or administer the service.

Requires Python 3.10+, systemd 247+ and outbound HTTPS to your core. No third-party
Python packages, inbound ports, AI runtime or API keys are needed. The service
runs as a restricted dynamic user, using systemd credentials and a private state
directory, with CPU and memory limits.

## Install

1. Register the SSH server in Briareus.
2. As an operator with an admin token, call
   `POST /api/v1/settings/ssh/servers/<id>/monitor-token`; this enables telemetry
   and returns a dedicated `token`, `endpoint`, `intervalSeconds` and other metadata.
3. Save that JSON privately on the target server, outside Git, with permissions 0600;
   the machine token grants only telemetry writes for that server, never SSH or
   Briareus user APIs.
4. Copy or clone this repository onto the target, and install with:

   ```sh
   sudo sh install.sh /absolute/path/to/private-config.json
   ```

The installer validates configuration first, installs the code under
`/usr/local/lib/briareus-monitor`, the config in `/etc/briareus-monitor/config.json`
and the systemd service, then starts it. It does not install packages or reboot
the server. Once the private config has been installed, remove your temporary
source copy securely through your normal admin workflow.

Use `systemctl status briareus-monitor` and `journalctl -u briareus-monitor` to
inspect the service. Logs report failures without tokens or resource payloads.
Stop it with `sudo systemctl disable --now briareus-monitor`. To revoke its access,
call `DELETE /api/v1/settings/ssh/servers/<id>/monitor-token`; disabling or removing
the registered SSH server also rejects uploads. Issuing a replacement token
revokes the previous one; install the new config and restart the service.

Updates are explicit: copy/checkout a reviewed version and rerun the installer
with `/etc/briareus-monitor/config.json`; there is no automatic remote updater.

## History and outages

Briareus stores a rolling 24-hour history in its own MySQL database and expires
older rows. The monitor also maintains a private SQLite spool for up to 24 hours
while the core/network is down, surviving service/server restarts. It sends old
samples first on recovery, in batches of ten, up to six batches per minute.
The queue is capped at 1440 samples and 16 MiB of payload; exceptionally large
snapshots may shorten the buffered window. No daemon can collect while its host
is powered off. Keep host clocks synchronized: Briareus rejects future timestamps
beyond 30 seconds and discards samples older than 24 hours.

CPU/network rates are computed centrally between snapshots, reset on a new boot
or counter reset, and unavailable until a valid baseline exists. Memory uses
`MemAvailable`. Disk statistics exclude network/automount and pseudo mounts.
Network is reported per interface, excluding loopback; do not sum virtual and
physical interfaces, which can double count traffic.

Protocol: `POST <endpoint>` with `Authorization: Bearer <machine-token>` and
`{"samples":[{"at":<Unix milliseconds>,"raw":<collector snapshot>}]}`. The core
acknowledges `{received, accepted}`. Retries are idempotent per minute and expired
samples are acknowledged but discarded. Redirects are refused so credentials
cannot be forwarded elsewhere; TLS certificates are verified normally.

Read current resources via `GET /api/v1/ssh/servers`, and history via
`GET /api/v1/ssh/servers/<id>/resources`, with an ordinary Briareus read token.

## Development

```sh
python3 -m unittest discover -s tests -v
systemd-analyze verify briareus-monitor.service
```

The code and installer are MIT licensed. The collector's Linux counter semantics
follow the [kernel proc documentation](https://docs.kernel.org/filesystems/proc.html).
