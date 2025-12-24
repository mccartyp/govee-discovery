# govee-discovery

A small Python toolchain for discovering and interrogating Govee devices that support the Govee LAN Control API.

This project is intended to act as a **registry builder** that can later feed an operational module (for example, an Art-Net → Govee bridge). Discovery and operational control are intentionally separate concerns.

---

## Directory Structure

```
govee-lan-discovery/
  pyproject.toml           Packaging and console script entrypoint
  README.md                Usage and project documentation
  LICENSE                  MIT license
  .gitignore               Ignored files for Python + runtime artifacts

  govee_discovery/
    __init__.py            Package version
    cli.py                 Command line interface (govee-discovery)
    net.py                 UDP socket helpers and port/group constants
    store.py               SQLite registry schema and query helpers
    discovery.py           LAN scan (multicast request + UDP/4002 listener)
    interrogate.py         Follow-on device interrogation (devStatus) and enrichment
```

---

## File Descriptions

### Root Files

- **pyproject.toml**  
  Python packaging metadata and console script entrypoint definition. Installs the `govee-discovery` command.

- **README.md**  
  Project overview, architecture, usage, and data model documentation.

- **LICENSE**  
  MIT license text.

- **.gitignore**  
  Ignores virtual environments, build artifacts, caches, SQLite databases, and logs.

### Package Files (`govee_discovery/`)

- **__init__.py**  
  Package version constant.

- **cli.py**  
  Command-line interface implementation providing:
  - `scan`: send multicast scan request(s) and listen for responses
  - `listen`: listen only for responses (no scan requests)
  - `interrogate`: query devices for `devStatus` and optionally normalize data
  - `dump`: dump database tables (`devices`, `events`, `interrogations`, `kv`) as JSON
  - `control`: send LAN control commands (on/off/color/brightness/color temperature)

- **net.py**  
  UDP constants (multicast group and ports) and helpers to create:
  - multicast sender socket (scan request)
  - listener socket (scan responses)
  - control socket (devStatus)

- **store.py**  
  SQLite schema and storage/query helpers. Persists:
  - `devices`: latest snapshot per device
  - `scan_events`: raw discovery packet history
  - `interrogations`: request/response history
  - `device_kv`: normalized key/value attributes
  - `device_tags`: optional tagging (future use)

- **discovery.py**  
  Discovery logic:
  - sends scan request to `239.255.255.250:4001`
  - listens for scan responses on UDP `4002`
  - stores raw packets and upserts devices into SQLite

- **interrogate.py**  
  Follow-on enrichment:
  - sends `devStatus` to each device on UDP `4003`
  - stores request/response history
  - optionally normalizes common fields into `device_kv`

- **control.py**  
  Control command support:
  - sends LAN control commands (on/off/color/brightness/color temperature) on UDP `4003`
  - optional response capture for debugging

---

## What It Does

### 1. Discovery

- Sends a multicast scan request to `239.255.255.250:4001`
- Listens for scan responses on UDP `4002`
- Saves raw scan packets and a normalized device registry into SQLite

### 2. Interrogation / Enrichment

- Queries each discovered device on UDP `4003` using `devStatus`
- Saves request/response pairs into SQLite
- Optionally normalizes key fields into a flexible key/value table (`device_kv`)

### 3. JSON Dump Utilities

- Dump `devices`, `events`, `interrogations`
- Dump `kv` entries per-device or globally, with optional key-prefix filtering

---

## Requirements

- Debian 13 (or any Linux with Python 3.11+)
- Network reachability to the IoT network where Govee devices live
- For discovery across VLANs, multicast + UDP/4002 must be permitted

If multicast is unavailable:
- Run discovery on a host in the device VLAN and copy the SQLite DB
- Or skip discovery entirely and use static IPs in the operational module

---

## Install (Editable / Development)

```
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

---

## Usage

All commands accept:

- `--db <path>`  
  SQLite registry DB path (default: `./govee_registry.sqlite`)

- `--bind-ip <ip>`  
  Local IPv4 to bind on multi-homed hosts

---

### Scan

Send multicast scan requests and listen for responses:

```
govee-discovery scan --db ./govee_registry.sqlite --duration 20 --verbose
```

Options:

- `--scan-repeat N` (default: 3)
- `--scan-interval SECONDS`
- `--resolve-mac` best-effort MAC lookup via `ip neigh`
- `--duration 0` run forever

---

### Listen Only

Listen for scan responses without sending scan packets:

```
govee-discovery listen --db ./govee_registry.sqlite --duration 0 --verbose
```

---

### Interrogate

Query all discovered devices using `devStatus`:

```
govee-discovery interrogate --db ./govee_registry.sqlite --verbose
```

Options:

- `--timeout SECONDS` (default: 2.0)
- `--ip A.B.C.D` (repeatable) interrogate IPs directly (bypass registry)
- `--only-ip A.B.C.D` (repeatable)
- `--no-enrich` do not normalize fields into `device_kv`

---

### Dump Registry

Dump devices:

```
govee-discovery dump devices --db ./govee_registry.sqlite --pretty
```

Dump scan events:

```
govee-discovery dump events --db ./govee_registry.sqlite --limit 200 --pretty
```

Dump interrogations:

```
govee-discovery dump interrogations --db ./govee_registry.sqlite --limit 200 --pretty
```

Dump key/value attributes:

```
govee-discovery dump kv --db ./govee_registry.sqlite --pretty
```

Dump kv for one device:

```
govee-discovery dump kv --db ./govee_registry.sqlite --device-id DEVICE_ID --pretty
```

Dump kv by prefix:

```
govee-discovery dump kv --db ./govee_registry.sqlite --device-id DEVICE_ID --key-prefix status. --pretty
```

---

### Control (LAN Commands)

Send control commands to a device using its IP or device ID (looked up from the registry):

```
govee-discovery control --ip 192.168.1.50 on
govee-discovery control --device-id ABCD1234 color red
govee-discovery control --ip 192.168.1.50 color #ff8800
govee-discovery control --ip 192.168.1.50 colorwc --kelvin 2700
govee-discovery control --ip 192.168.1.50 color red --color-cmd colorwc --kelvin 3200
govee-discovery control --ip 192.168.1.50 color red --color-cmd setColor --color-scale 100
govee-discovery control --ip 192.168.1.50 brightness 75
govee-discovery control --ip 192.168.1.50 color-temp 3500
govee-discovery control --ip 192.168.1.50 colorwc --kelvin 4000 --color #ffaa88
govee-discovery control --ip 192.168.1.50 color-probe --stop-on-success
```

Notes:
- Color accepts common names (red, green, blue, white, warmwhite, yellow, orange, purple, pink, cyan, magenta)
  or hex RGB strings (`RRGGBB` / `#RRGGBB`).
- `colorwc` sends combined RGB + `colorTemInKelvin` payloads for WW/CW or hybrid models (some ignore the RGB value).
  If `--color` is omitted, a warm or cool white is used by default based on the requested Kelvin.
- `--color-cmd {color,colorwc,setColor}` picks the command name used in the payload. Try `colorwc` when a device
  needs the Kelvin field to accept color changes, or `setColor` if `color` appears to be ignored.
- `--kelvin` can be supplied to the `color` action when using `--color-cmd colorwc` to send combined color/Kelvin
  payloads.
- `--color-scale` scales RGB values to `0-100` instead of `0-255` for models that expect the smaller range.
- Use `--no-wait` to skip waiting for device responses.

#### Probe control payloads

Use `color-probe` to iterate through common LAN control payload variants to learn which combination your device
responds to. The probe tries:

- Command names: `color`, `colorwc`, `setColor`, `setColorWC`
- RGB scales: `0-255` and `0-100`
- Kelvin values: defaults to `3000`, `4000`, `6500`; configurable via `--kelvin`
- With and without Kelvin for pure RGB commands (unless `--require-kelvin` is set)

Example (stops after the first response):

```
govee-discovery control --ip 192.168.1.50 color-probe --stop-on-success --verbose
[probe] ip=192.168.1.50 cmd=color scale=255 kelvin=- color=red payload={"msg":{"cmd":"color","data":{"color":{"r":255,"g":0,"b":0}}}}
[probe] ip=192.168.1.50 cmd=colorwc scale=255 kelvin=3000 color=red payload={"msg":{"cmd":"colorwc","data":{"color":{"r":255,"g":0,"b":0},"colorTemInKelvin":3000}}}
[probe] cmd=color scale=255 kelvin=- color=red status=timeout
[probe] cmd=colorwc scale=255 kelvin=3000 color=red status=resp code=200
cmd       scale  kelvin  color  status
--------- ------ ------- ------ ---------
color     255    -       red    timeout
colorwc   255    3000    red    resp code=200
```

Options:
- `--color` Repeatable list of test colors (name or hex). Default: `red`, `green`, `blue`.
- `--kelvin` Repeatable list of Kelvin values. Default: `3000`, `4000`, `6500`.
- `--require-kelvin` Skip pure RGB payloads (only send combined RGB/Kelvin variants).
- `--stop-on-success` Stop probing once any response is received.
- Each attempt is logged (command, scale, Kelvin, color, status) and a 1-second pause is inserted between attempts
  to avoid flooding devices. Missing replies are recorded as `timeout` instead of failing the probe.

---

## SQLite Data Model

### devices
Latest per-device snapshot from discovery and interrogation. Stores raw JSON from last scan and last `devStatus`.

### scan_events
Raw discovery packets (verbatim), timestamp, and sender address.

### interrogations
Request/response history per device for follow-on queries.

### device_kv
Flexible normalized attributes (future-proof; avoids migrations).

### device_tags
Optional tagging/grouping (future use).

---

## Notes

- MAC address resolution is best-effort and typically unavailable across routed VLANs.
- This project includes basic LAN control for testing; full operational control can live in a separate module.

---

## License

MIT
