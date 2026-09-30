# fprime-cosmos: Tools to Make Open C3 COSMOS Integration Easier

Runs an [F Prime](https://github.com/nasa/fprime) deployment against [OpenC3 COSMOS](https://openc3.com).

`fprime-cosmos` reads the F Prime JSON topology dictionary, generates a native COSMOS plugin (commands,
channelized telemetry and packetized telemetry), installs it into a running COSMOS through the plugin API,
and starts `fprime-comm-bridge` (from `fprime-gds`) to move packets between the flight software and COSMOS.
No Ruby toolchain, no hand-edited plugin files and no knowledge of COSMOS internals are required.

```text
F Prime deployment  <-- framing / transport -->  fprime-comm-bridge  <-- UDP, F Prime packets -->  COSMOS
                                                 (owns framing)                                    (this plugin)
```

## Installation

```bash
pip install fprime-cosmos
```

This pulls in `fprime-gds`, which provides the dictionary loaders and `fprime-comm-bridge`. The bridge is
merged on the `fprime-gds` development branch; until a release ships it (4.3.1 does not), install that first:

```bash
pip install "git+https://github.com/nasa/fprime-gds.git@devel"
```

Prefer `$OPENC3_API_PASSWORD` over `--cosmos-password`; command-line arguments are visible to other users of the
machine. The default `--cosmos-url` is plain HTTP on `localhost`; use an `https://` URL for a remote COSMOS.

## Quick start

1. Start COSMOS (for example the [cosmos-project](https://github.com/OpenC3/cosmos-project) with
   `./openc3.sh run`). COSMOS must be able to receive UDP telemetry on port 50000 from the bridge, so publish
   that port on the `openc3-operator` container (add to `compose.yaml`/an override file):

   ```yaml
   services:
     openc3-operator:
       ports:
         - "127.0.0.1:50000:50000/udp"
   ```

2. Run the launcher with the deployment's dictionary and the COSMOS password:

   ```bash
   export OPENC3_API_PASSWORD=...
   fprime-cosmos --dictionary build-artifacts/Linux/MyDeployment/dict/MyDeploymentTopologyDictionary.json
   ```

   The launcher

   - generates the plugin into `./openc3-plugin/` and packages it as a gem,
   - installs (or upgrades) the plugin in COSMOS, skipping the install when the same dictionary is already
     installed (the dictionary digest is part of the gem version),
   - starts `fprime-comm-bridge` with `--framing-selection space-packet-space-data-link`,
   - with `--app path/to/MyDeployment`, starts the deployment once the bridge is up, connected to the
     bridge's `tcp-fast-server` (`-a 127.0.0.1 -p 50000` by default; `--app-arguments` overrides), and stops
     it again when the bridge exits or on Ctrl-C. The deployment's output goes to `<output>/logs/`.

   Anything the launcher does not recognise is forwarded to `fprime-comm-bridge`, so the flight link is
   configured exactly as for the GDS, for example:

   ```bash
   fprime-cosmos --dictionary ...json --communication-selection tcp-fast-client --tcp-fast-address 192.168.1.10
   fprime-cosmos --dictionary ...json --framing-selection fprime     # deployment uses F Prime framing
   ```

3. Open COSMOS: the `FPRIME` target appears with one command per F Prime command, one telemetry packet per
   channel and one per telemetry packet-set entry.

### Options

| Option | Purpose |
| --- | --- |
| `--target-name NAME` | COSMOS target name (default `FPRIME`) |
| `--packet-set-name NAME` | Packet set to use when the dictionary defines several |
| `--app PATH` | Deployment binary to start alongside the bridge |
| `--app-arguments "..."` | Arguments for `--app` (default `-a <tcp-fast address> -p <tcp-fast port>`) |
| `--logs DIR` | Where the deployment log is written (default `<output>/logs`) |
| `--cosmos-url URL` | COSMOS base URL (default `http://localhost:2900`) |
| `--cosmos-password`, `$OPENC3_API_PASSWORD` | COSMOS password (set on first use if COSMOS has none) |
| `--cosmos-scope SCOPE` | COSMOS scope (default `DEFAULT`) |
| `--cosmos-variable NAME=VALUE` | Override a plugin variable (see below) |
| `--force-install` | Reinstall even when the dictionary digest matches |
| `--skip-install` / `--skip-bridge` | Generate only / do not start the bridge |

Plugin variables (all defaults match the bridge defaults):

| Variable | Default | Meaning |
| --- | --- | --- |
| `fprime_target_name` | `FPRIME` | COSMOS target name |
| `fprime_bridge_host` | `host.docker.internal` | Bridge host as seen from the COSMOS containers |
| `fprime_bridge_port` | `50001` | Bridge `--udp-fast-recv-port` (commands) |
| `fprime_cosmos_port` | `50000` | Bridge `--udp-fast-send-port` (telemetry) |

Commands reach the bridge from the COSMOS container addresses. When COSMOS is local, `fprime-cosmos` reads
those addresses from `docker ps`/`docker inspect` (plus the Docker bridge gateways) and starts the bridge with
`--udp-fast-bind-address <docker0 address> --udp-fast-allowed-source <addresses>`, i.e. the bridge listens only
on the address the containers know as `host.docker.internal` and only accepts datagrams from them (the bridge
command port is otherwise unauthenticated). Restart the launcher if the containers are recreated with new
addresses. Passing either option yourself disables this defaulting, for example
`--udp-fast-bind-address 192.168.1.5 --udp-fast-allowed-source 192.168.1.20` for a remote COSMOS (and set
`--cosmos-variable fprime_bridge_host=192.168.1.5` so COSMOS sends commands to that address).

One plugin serves one COSMOS target: installing a dictionary for a target that another F Prime plugin already
serves upgrades that plugin in place (COSMOS forbids two plugins defining the same target). Use
`--target-name` to run several deployments side by side.

### Generating the plugin on its own

```bash
fprime-to-cosmos MyDeploymentTopologyDictionary.json -o openc3-plugin [--target-name NAME] [--no-gem] [--install]
```

By default this only writes `openc3-plugin/openc3-cosmos-fprime-<deployment>/` and the corresponding `.gem`,
which can be installed through the COSMOS Admin tool or `openc3cli load`. With `--install` the gem is also
installed into COSMOS (same `--cosmos-*`/`--force-install` options as the launcher).

## What is generated

All widths and constants come from the dictionary's `typeDefinitions` and `constants` (descriptor, opcode,
channel and packet identifier types, `FwSizeStoreType` string lengths, boolean encodings, time base enum).

- **Commands** – `FPRIME_DESCRIPTOR`, `FPRIME_OPCODE` and the serialized arguments. Enums and booleans get
  `STATE`s, strings are a length item plus a `VARIABLE_BIT_SIZE` string, structs are flattened to
  `name.member`, arrays of numbers become COSMOS arrays and other arrays are flattened to `name[i]`.
- **Channelized telemetry** – the `FPRIME_CHANNELS` parent packet carries the concatenated channel records and a
  `SUBPACKETIZER` (`fprime_subpacketizer.py`) splits it into one `SUBPACKET` per channel with the channel id,
  F Prime time (and `FPRIME_TIME`, that time as Unix time), the value, `FORMAT_STRING` and `LIMITS` from the
  dictionary. COSMOS stamps packets with the received time; rename `FPRIME_TIME` to `PACKET_TIME` in a copy of
  the plugin to stamp them with F Prime time instead (only meaningful for `TB_WORKSTATION_TIME`).
- **Packetized telemetry** – one packet per entry of the selected `telemetryPacketSets` set.
- `FPRIME_UNKNOWN` catches packets the plugin does not model (events, files, data products).

Events are not generated in this version.

## Development

```bash
python -m venv venv && . venv/bin/activate
pip install -e .[dev]
pytest
ruff check src tests && ruff format --check src tests
python tests/update_golden.py   # after intentional emitter changes
```

## Attribution

`fprime_subpacketizer.py` and the plugin variable layout derive from
[openc3-cosmos-fprime](https://github.com/OpenC3/openc3-cosmos-fprime), Copyright 2026 OpenC3, Inc., MIT
License; see [NOTICE](NOTICE). fprime-cosmos itself is licensed under the Apache License 2.0 ([LICENSE](LICENSE)).
