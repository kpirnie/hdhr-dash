# HDHR Dash

[![Build Container Image](https://github.com/kpirnie/hdhr-dash/actions/workflows/build-image.yml/badge.svg)](https://github.com/kpirnie/hdhr-dash/actions/workflows/build-image.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-kpirnie%2Fhdhr--dash-2B8EFF?logo=github)](https://github.com/kpirnie/hdhr-dash/pkgs/container/hdhr-dash)
[![Podman](https://img.shields.io/badge/Runs%20on-Podman-892CA0?logo=podman)](https://podman.io/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

A self-hosted web dashboard and **Progressive Web App** for [SiliconDust HDHomeRun](https://www.silicondust.com/) network tuners and DVR recording engines — live tuner gauges, an in-browser ATSC 1.0 player, channel lineup management with scanning, DVR library management, and hardware diagnostics.

It ships as a small container image and talks to your HDHomeRun through a built-in, LAN-only device proxy, so it works over HTTPS and behind a reverse proxy.

> This is a fork of [jay0lee/hdhr-dash](https://github.com/jay0lee/hdhr-dash), restyled to match the [PodNest](https://podnest.app) look and reworked to run as a container.

---

## Features

### Tuners

- **Live tuner cards** — streaming / idle / recording status, channel name and network logo, virtual and physical **RF channel**, **UHF / VHF** band, and broadcast frequency.
- **Clients and bitrate** — which device is streaming from each tuner, live network rate, and detection of multiple clients sharing one tuner.
- **Signal meters** — Signal Strength, SNR Quality, and Symbol Quality, color-coded good / fair / poor.
- **Signal graph** — open any tuner for a real-time chart with min / avg / max per metric and adjustable sample rate, useful for aiming an antenna.
- **Configurable polling** — 1s, 2.5s, 5s, or off; polling pauses while the tab is hidden.

### Channels

- **Full lineup** — every found channel is shown by default, with badges for band, ATSC 1.0 / 3.0, HD / SD, favorite / hidden, and DRM.
- **Favorite and hide toggles** — mark channels as favorites or hide them right from the table; changes are written to the tuner.
- **Channel scan** — rescan the lineup from the dashboard, pick the signal source (Antenna / Cable), and watch live progress. You're warned before a scan interrupts active streams or recordings, and a scan started elsewhere is picked up automatically.
- **Search and filters** — by name, callsign, or number; filter by status, band, format, and DRM.
- **Playback and export** — play ATSC 1.0 channels in the browser, copy a direct stream URL, download a per-channel `.m3u`, or export the whole lineup as an `#EXTM3U` playlist.

### DVR & Recordings

- **Storage gauge** for the attached drive or network storage engine.
- **Series library** with poster art, episode counts, latest recording date, and size.
- **Episodes** — download the raw `.mpg`, copy the direct link, stream via `.m3u`, or delete; currently-playing recordings are flagged.
- **Scheduled rules** from the HDHomeRun cloud DVR, with priority ordering.

### System & Diagnostics

- **Hardware overview** — model, device ID, firmware (with update check), tuner and channel counts, active clients and recordings, storage.
- **Multi-device support** — save several tuners and switch between them.
- **Diagnostic export** — gathers the raw device JSON feeds with optional redaction of serials, auth tokens, and IP addresses, ready to paste into a forum or issue.

---

## Quick Start

```bash
podman run -d --name hdhr-dash \
  -p 8443:8443 \
  -v hdhr-dash-certs:/certs \
  ghcr.io/kpirnie/hdhr-dash:latest
```

Open `https://<host>:8443`, accept the self-signed certificate warning (see [HTTPS & Certificates](#https--certificates)), then go to the **System** tab and add your HDHomeRun's IP address (e.g. `192.168.1.100`).

---

## Running

### Podman Compose

A sample is provided at [`deploy/compose.yaml`](deploy/compose.yaml):

```bash
podman-compose -f deploy/compose.yaml up -d
```

Set `HDHR_PORT` / `HDHR_HTTP` in your environment or in an `.env` file next to the compose file.

### systemd

Sample units are provided for both rootful and rootless Podman.

**Rootful** — [`deploy/hdhr-dash.service`](deploy/hdhr-dash.service):

```bash
sudo cp deploy/hdhr-dash.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hdhr-dash.service
```

**Rootless** — [`deploy/hdhr-dash.user.service`](deploy/hdhr-dash.user.service):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/hdhr-dash.user.service ~/.config/systemd/user/hdhr-dash.service
systemctl --user daemon-reload
systemctl --user enable --now hdhr-dash.service
sudo loginctl enable-linger "$USER"
```

Edit the `Environment=` lines in the unit to change the image, port, or HTTP mode. Rootless containers can't bind ports below 1024 unless `net.ipv4.ip_unprivileged_port_start` is lowered.

### Without a container

The server is a single Python 3 script with no dependencies beyond the standard library (and `openssl` for generating a certificate):

```bash
git clone https://github.com/kpirnie/hdhr-dash.git
cd hdhr-dash
python3 scripts/serve-https.py              # HTTPS on 8443 with a self-signed cert
python3 scripts/serve-https.py 8080 --http  # plain HTTP on 8080
```

| Option | Description |
| --- | --- |
| `port` | Port to listen on (default `8443`, env `HDHR_PORT`) |
| `--http` | Serve plain HTTP (env `HDHR_HTTP`) |
| `--bind`, `-b` | Address to bind (default `0.0.0.0`) |
| `--cert` / `--key` | Certificate and key paths; generated there if they don't exist |
| `--temp` | Use a throwaway certificate that's deleted on exit |

---

## Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `HDHR_PORT` | `8443` | Port the dashboard listens on inside the container. The samples map the same port on the host. |
| `HDHR_HTTP` | `false` | `true` serves plain HTTP — use this behind a TLS-terminating reverse proxy. `false` serves HTTPS with a self-signed certificate. |
| `/certs` volume | — | Holds `cert.pem` / `key.pem`. Generated on first start if missing; drop in your own to replace them. |

### HTTPS & Certificates

Browsers only enable the clipboard, service-worker caching, and PWA installation on a **trusted** HTTPS origin. You have three options:

1. **Reverse proxy (recommended)** — run with `HDHR_HTTP=true` behind a proxy with a real certificate. All features work, including installing the dashboard as an app.
2. **Your own certificate** — put a trusted `cert.pem` and `key.pem` in the `/certs` volume.
3. **Self-signed (default)** — works for everything except the trusted-origin features above. On first visit, accept the warning:
   - **Chrome / Edge:** Advanced → Proceed (or type `thisisunsafe` on the warning page)
   - **Firefox:** Advanced → Accept the Risk and Continue
   - **Safari:** Show Details → visit this website

### `.local` names and host networking

The container resolves HDHomeRun `.local` (mDNS) hostnames only when it shares the host's network. With normal bridge networking, any `.local` address the tuner advertises (such as its storage URL) is automatically rewritten to the IP of the device you added, so **add your tuner by IP address** in that case.

To enable full mDNS resolution (including `hdhomerun.local` discovery), run with host networking:

- **Compose:** comment out `ports:` and uncomment `network_mode: host`.
- **systemd / podman run:** replace `-p ${HDHR_PORT}:${HDHR_PORT}` with `--network host`.

With host networking the dashboard listens directly on `HDHR_PORT` on the host.

---

## How It Works

The dashboard is a static web app served by `scripts/serve-https.py`, which also exposes a same-origin device proxy at `/api/proxy`. Every request to the tuner — JSON APIs, live streams, recording downloads, and commands like delete, scan, and favorite — goes through that proxy, so:

- the page can be served over HTTPS without the browser blocking `http://` tuner requests as mixed content,
- it works unchanged behind a reverse proxy, and
- no CORS issues come up with the tuner's DVR endpoints.

The proxy only forwards plain-HTTP requests to **private, loopback, or `.local`** addresses; anything else is refused.

**Copy URL** and the `.m3u` exports still hand out the tuner's direct `http://` stream URLs, since media players like VLC fetch those themselves on your LAN.

---

## Building the Image

```bash
podman build -f Containerfile -t hdhr-dash:local .
podman run --rm -it -p 8443:8443 -v hdhr-dash-certs:/certs hdhr-dash:local
```

GitHub Actions builds a multi-arch (`linux/amd64`, `linux/arm64`) image and pushes it to `ghcr.io/kpirnie/hdhr-dash` on every push to `main`, and again after each automated station-database update. Tags:

- `latest` — the current `main`
- `sha-<commit>` — a specific build

---

## Need-to-Know

### UHF vs. VHF and RF channels

Every channel has a **virtual** number (e.g. `10.1`, the one on your guide) and a **physical RF** channel (e.g. `RF 28`, the actual frequency). VHF (RF 2–13) needs larger antenna elements and is more prone to household interference; UHF (RF 14–36) uses smaller, directional elements. If specific channels have poor reception, check their band against what your antenna supports.

### ATSC 1.0 vs. ATSC 3.0 and DRM

- **ATSC 1.0** — standard digital broadcast. Plays in the browser, streams to VLC, and records without restriction.
- **ATSC 3.0 (NextGen TV)** — 4K / HDR, HEVC video, AC-4 audio. The in-browser player supports ATSC 1.0 only; use Copy URL or `.m3u` to play ATSC 3.0 in an external player.
- **DRM** — some broadcasters encrypt ATSC 3.0 streams. See the [SiliconDust ATSC 3.0 DRM forum](https://forum.silicondust.com/forum/viewforum.php?f=133) for current support.

### Channel scans

A scan takes over **every** tuner and interrupts any live streams or recordings in progress. The lineup reloads automatically when the scan finishes.

### DVR requirements

The Recordings tab activates when the device advertises a recording engine — a USB drive on an HDHomeRun FLEX 4K, or the HDHomeRun DVR service running on a NAS / PC. Without one, everything else still works.

---

## Privacy & Security

- **No analytics, tracking, or accounts.**
- **Tuner traffic stays on your LAN** — the built-in proxy only talks to private / local addresses.
- **External requests:** fonts from Google Fonts, the UIkit UI library from jsDelivr, the firmware check against SiliconDust, scheduled DVR rules from `api.hdhomerun.com`, and a weekly station-database refresh from this repository.
- **Safe diagnostics sharing** — the log collector can redact serial numbers, auth tokens, and IP addresses before you share them.

---

## Credits & License

Originally created by [jay0lee](https://github.com/jay0lee/hdhr-dash). Container packaging, device proxy, channel scanning, lineup management, and PodNest restyle by [Kevin Pirnie](https://github.com/kpirnie).

Distributed under the Apache License 2.0. See [LICENSE](LICENSE).