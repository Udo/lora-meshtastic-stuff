# Meshtastic Protocol Primer

## Purpose

Meshtastic carries many kinds of application traffic over the same mesh. At the app layer, the most important discriminator is the packet `portnum`. The `portnum` tells you how to interpret the payload bytes inside the packet.

This primer focuses on those app-level payloads:

- what the outer packet envelope looks like
- which `portnum` values matter most
- what the common protobuf message structures contain
- how that maps onto the tools and plugins

This is not a primer on the radio PHY, LoRa airtime, channel crypto internals, or the firmware routing implementation.

## Mental Model

Think of a Meshtastic packet in layers:

1. `ToRadio` / `FromRadio`
   Host-to-node or node-to-host framing on the serial/TCP API.
2. `MeshPacket`
   The mesh transport envelope: source, destination, IDs, hop data, priority, and either decoded or encrypted payload.
3. `Data`
   The decoded app payload wrapper: `portnum`, raw `payload`, and reply metadata.
4. App-specific payload
   The protobuf or byte format implied by that `portnum`.

That is also how this repo is structured:

- the proxy and broker reason about `ToRadio` / `FromRadio` and `MeshPacket`
- the plugin system dispatches primarily by `Data.portnum`
- tools like `messages`, `status`, and `protocol` decode the app payload according to the selected protocol

## Packet Envelope

### `ToRadio`

`ToRadio` is what a client sends to the node. Important variants:

- `packet`
  A `MeshPacket` to inject into the mesh.
- `want_config_id`
  Request the node to stream config state.
- `disconnect`
  Host-side disconnect signal.
- `heartbeat`
  Client heartbeat at the host API level, not to be confused with app-level protocol heartbeat.

### `FromRadio`

`FromRadio` is what the node sends back to the client. Important variants:

- `packet`
  A `MeshPacket` received from the mesh or generated locally by the node.
- `my_info`
  Local node identity and basic session info.
- `node_info`
  A `NodeInfo` snapshot for a known node.
- `config`
  Device config payload.
- `moduleConfig`
  Module config payload.
- `channel`
  Channel definition payload.
- `metadata`
  Device metadata.
- `log_record`
  Node-side log line.

## `MeshPacket`

`MeshPacket` is the actual mesh envelope. The fields that matter most operationally are:

- `from`
  Source node number.
- `to`
  Destination node number.
- `channel`
  Channel index.
- `decoded`
  Decoded `Data` payload, if the host can see it.
- `encrypted`
  Raw encrypted bytes when only ciphertext is available.
- `id`
  Packet identifier.
- `rx_time`
  Receive timestamp.
- `rx_snr`
  Receive SNR.
- `rx_rssi`
  Receive RSSI.
- `hop_limit`
  Remaining hops.
- `hop_start`
  Starting hop budget.
- `want_ack`
  Whether an acknowledgement is requested.
- `priority`
  Packet priority.
- `delayed`
  Delayed-delivery category.
- `via_mqtt`
  Whether the packet arrived via MQTT bridge rather than pure RF.
- `public_key`
  Public key material when present.
- `next_hop`
  Next-hop routing hint.
- `relay_node`
  Relay metadata.

The payload itself is a oneof:

- `decoded`
  A `Data` wrapper the host can interpret.
- `encrypted`
  Opaque bytes the host cannot decode directly.

### Priority

`MeshPacket.Priority` currently includes:

- `MIN`
- `BACKGROUND`
- `DEFAULT`
- `RELIABLE`
- `RESPONSE`
- `HIGH`
- `ALERT`
- `ACK`

### Delay Category

`MeshPacket.Delayed` currently includes:

- `NO_DELAY`
- `DELAYED_BROADCAST`
- `DELAYED_DIRECT`

## `Data`

`Data` is the decoded app wrapper inside `MeshPacket.decoded`.

Important fields:

- `portnum`
  The app protocol selector.
- `payload`
  Raw payload bytes for that protocol.
- `want_response`
  App-level request/response hint.
- `dest`
  Application destination override when present.
- `source`
  Application source override when present.
- `request_id`
  Request correlation ID.
- `reply_id`
  Reply correlation ID.
- `emoji`
  Emoji reaction helper field.
- `bitfield`
  Additional compact flags.

`portnum` and `payload` are the key pieces.

## Worked Envelope Example

A concrete decoded `MeshPacket` carrying a `TEXT_MESSAGE_APP` payload:

```text
from: 305419896
to: 4294967295
decoded {
  portnum: TEXT_MESSAGE_APP
  payload: "hello mesh"
}
id: 16909060
hop_limit: 3
```

Serialized protobuf bytes for that `MeshPacket`:

```text
0d7856341215ffffffff220e0801120a68656c6c6f206d65736835040302014803
```

How to read it:

- `from = 305419896`
  Source node number `0x12345678`
- `to = 4294967295`
  Broadcast-style destination
- `decoded.portnum = TEXT_MESSAGE_APP`
  The payload should be interpreted as a text message
- `decoded.payload = "hello mesh"`
  The app payload bytes
- `id = 16909060`
  Packet ID `0x01020304`
- `hop_limit = 3`
  Remaining hop budget

## Important `portnum` Values

The current Meshtastic protobuf enum includes many app ports. The most important ones are:

- `TEXT_MESSAGE_APP` = `1`
- `POSITION_APP` = `3`
- `NODEINFO_APP` = `4`
- `ROUTING_APP` = `5`
- `ADMIN_APP` = `6`
- `STORE_FORWARD_APP` = `65`
- `TELEMETRY_APP` = `67`
- `TRACEROUTE_APP` = `70`
- `NEIGHBORINFO_APP` = `71`
- `PRIVATE_APP` = `256`

Other defined ports include:

- `REMOTE_HARDWARE_APP` = `2`
- `TEXT_MESSAGE_COMPRESSED_APP` = `7`
- `WAYPOINT_APP` = `8`
- `AUDIO_APP` = `9`
- `DETECTION_SENSOR_APP` = `10`
- `ALERT_APP` = `11`
- `KEY_VERIFICATION_APP` = `12`
- `REPLY_APP` = `32`
- `IP_TUNNEL_APP` = `33`
- `PAXCOUNTER_APP` = `34`
- `SERIAL_APP` = `64`
- `RANGE_TEST_APP` = `66`
- `ZPS_APP` = `68`
- `SIMULATOR_APP` = `69`
- `ATAK_PLUGIN` = `72`
- `MAP_REPORT_APP` = `73`
- `POWERSTRESS_APP` = `74`
- `RETICULUM_TUNNEL_APP` = `76`
- `ATAK_FORWARDER` = `257`

## Core Built-In Payloads

### `TEXT_MESSAGE_APP`

This is the public text-chat port. The payload is plain text bytes. `messages sync` and `messages send` treat it as UTF-8 text.

Operationally:

- public broadcast chat depends on this port
- our `TEXT_MESSAGE_APP` plugin archives these payloads for store-and-forward replay
- the payload structure is simple compared with the protobuf-heavy ports below

Example payload bytes:

```text
68656c6c6f206d657368
```

Interpreted as UTF-8 text:

```text
hello mesh
```

### `POSITION_APP`

Position payloads decode to `Position`.

Important `Position` fields:

- `latitude_i`
- `longitude_i`
- `altitude`
- `time`
- `location_source`
- `altitude_source`
- `timestamp`
- `timestamp_millis_adjust`
- `altitude_hae`
- `altitude_geoidal_separation`
- `PDOP`
- `HDOP`
- `VDOP`
- `gps_accuracy`
- `ground_speed`
- `ground_track`
- `fix_quality`
- `fix_type`
- `sats_in_view`
- `sensor_id`
- `next_update`
- `seq_number`
- `precision_bits`

The coordinate fields are the integer-scaled Meshtastic representation, not native floating-point latitude/longitude.

If you are decoding one of these by hand, the key step is converting `latitude_i` and `longitude_i` back into normal latitude/longitude units using the Meshtastic position scaling convention.

### `NODEINFO_APP`

Node identity payloads center on `NodeInfo`, which embeds `User` and may also embed `Position` and `DeviceMetrics`.

Important `NodeInfo` fields:

- `num`
  Node number.
- `user`
  User-facing identity data.
- `position`
  Latest position snapshot.
- `snr`
  Last observed SNR.
- `last_heard`
  Last-heard timestamp.
- `device_metrics`
  Embedded device telemetry snapshot.
- `channel`
  Channel index.
- `via_mqtt`
  MQTT-origin indicator.
- `hops_away`
  Known hop distance.
- `is_favorite`
- `is_ignored`
- `is_key_manually_verified`

Important `User` fields inside it:

- `id`
- `long_name`
- `short_name`
- `macaddr`
- `hw_model`
- `is_licensed`
- `role`
- `public_key`
- `is_unmessagable`

This is the main source of names, hardware model, and public key data that tools present as “node identity.”

### `ROUTING_APP`

Routing payloads decode to `Routing`.

`Routing` is a oneof over:

- `route_request`
  `RouteDiscovery`
- `route_reply`
  `RouteDiscovery`
- `error_reason`
  Routing error enum

`RouteDiscovery` contains:

- `route`
- `snr_towards`
- `route_back`
- `snr_back`

And the routing error enum includes:

- `NO_ROUTE`
- `GOT_NAK`
- `TIMEOUT`
- `MAX_RETRANSMIT`
- `TOO_LARGE`
- `NO_RESPONSE`
- `DUTY_CYCLE_LIMIT`
- `BAD_REQUEST`
- `NOT_AUTHORIZED`
- `PKI_FAILED`

This is housekeeping/control traffic.

### `ADMIN_APP`

`ADMIN_APP` is the mutating control plane. Config writes and owner/channel changes happen here.

`AdminMessage` is a large oneof-based command/response structure.

Important request/response fields:

- `session_passkey`
  Required to authorize mutating follow-up operations after a read response.
- `get_owner_request`
- `get_owner_response`
- `get_config_request`
- `get_config_response`
- `get_module_config_request`
- `get_module_config_response`
- `get_channel_request`
- `get_channel_response`
- `get_device_metadata_request`
- `get_device_metadata_response`
- `get_device_connection_status_request`
- `get_device_connection_status_response`
- `get_ui_config_request`
- `get_ui_config_response`

Mutating fields:

- `set_owner`
- `set_channel`
- `set_config`
- `set_module_config`
- `set_fixed_position`
- `remove_fixed_position`
- `set_favorite_node`
- `remove_favorite_node`
- `set_ignored_node`
- `remove_ignored_node`
- `remove_by_nodenum`
- `nodedb_reset`
- `factory_reset_device`
- `factory_reset_config`
- `reboot_seconds`
- `shutdown_seconds`

Config selection enums exposed through admin:

- `ConfigType`
  `DEVICE_CONFIG`, `POSITION_CONFIG`, `POWER_CONFIG`, `NETWORK_CONFIG`, `DISPLAY_CONFIG`, `LORA_CONFIG`, `BLUETOOTH_CONFIG`, `SECURITY_CONFIG`, `SESSIONKEY_CONFIG`, `DEVICEUI_CONFIG`
- `ModuleConfigType`
  `MQTT_CONFIG`, `SERIAL_CONFIG`, `EXTNOTIF_CONFIG`, `STOREFORWARD_CONFIG`, `RANGETEST_CONFIG`, `TELEMETRY_CONFIG`, `CANNEDMSG_CONFIG`, `AUDIO_CONFIG`, `REMOTEHARDWARE_CONFIG`, `NEIGHBORINFO_CONFIG`, `AMBIENTLIGHTING_CONFIG`, `DETECTIONSENSOR_CONFIG`, `PAXCOUNTER_CONFIG`

This is why the broker guards `ADMIN_APP`: it is where operator intent turns into actual node mutation.

Example admin payload requesting the store-forward module config:

```text
get_module_config_request: STOREFORWARD_CONFIG
```

Serialized protobuf bytes:

```text
3803
```

Shows how compact many protobuf control messages are on the wire. The semantic meaning comes from the selected oneof field and enum value.

### `TELEMETRY_APP`

Telemetry payloads decode to `Telemetry`, which is a timestamp plus a oneof variant.

`Telemetry` variants currently include:

- `device_metrics`
- `environment_metrics`
- `air_quality_metrics`
- `power_metrics`
- `local_stats`
- `health_metrics`
- `host_metrics`

Common telemetry structures:

`DeviceMetrics`

- `battery_level`
- `voltage`
- `channel_utilization`
- `air_util_tx`
- `uptime_seconds`

`EnvironmentMetrics`

- `temperature`
- `relative_humidity`
- `barometric_pressure`
- `gas_resistance`
- `voltage`
- `current`
- `iaq`
- `distance`
- `lux`
- `white_lux`
- `ir_lux`
- `uv_lux`
- `wind_direction`
- `wind_speed`
- `weight`
- `wind_gust`
- `wind_lull`
- `radiation`
- `rainfall_1h`
- `rainfall_24h`
- `soil_moisture`
- `soil_temperature`

`AirQualityMetrics`

- `pm10_standard`
- `pm25_standard`
- `pm100_standard`
- `pm10_environmental`
- `pm25_environmental`
- `pm100_environmental`
- `particles_03um`
- `particles_05um`
- `particles_10um`
- `particles_25um`
- `particles_50um`
- `particles_100um`
- `co2`

`PowerMetrics`

- `ch1_voltage`
- `ch1_current`
- `ch2_voltage`
- `ch2_current`
- `ch3_voltage`
- `ch3_current`

Module config lives under `ModuleConfig.telemetry`, including:

- `device_update_interval`
- `environment_update_interval`
- `environment_measurement_enabled`
- `air_quality_enabled`
- `air_quality_interval`
- `power_measurement_enabled`
- `power_update_interval`
- `health_measurement_enabled`
- `health_update_interval`

This repo’s `status telemetry` command works directly against this protocol.

Example telemetry payload:

```text
time: 1710000000
device_metrics {
  battery_level: 87
  voltage: 4.110000133514404
  uptime_seconds: 86400
}
```

Serialized protobuf bytes:

```text
0d8087ec65120b0857151f8583402880a305
```

How to read it conceptually:

- `time`
  Sample timestamp
- `device_metrics`
  The active oneof branch in this `Telemetry` message
- `battery_level = 87`
  Battery percentage
- `voltage = 4.11`
  Battery or device voltage
- `uptime_seconds = 86400`
  Uptime in seconds

### `NEIGHBORINFO_APP`

Neighbor info payloads decode to `NeighborInfo`.

Important fields:

- `node_id`
- `last_sent_by_id`
- `node_broadcast_interval_secs`
- `neighbors`

This is neighbor/RF topology information.

Relevant module config lives under `ModuleConfig.neighbor_info`:

- `enabled`
- `update_interval`
- `transmit_over_lora`

### `TRACEROUTE_APP`

Traceroute uses the route-discovery structures to inspect path behavior across the mesh. We treat it as route-debugging traffic rather than a user-content protocol.

### `STORE_FORWARD_APP`

`STORE_FORWARD_APP` is a request/response protocol encoded by the `StoreAndForward` message.

Top-level fields:

- `rr`
  Request/response enum.
- `stats`
  `Statistics`
- `history`
  `History`
- `heartbeat`
  `Heartbeat`
- `text`
  Raw text bytes

The message type is mostly driven by `rr`.

#### Request/Response Enum

Router-to-client values:

- `ROUTER_ERROR`
- `ROUTER_HEARTBEAT`
- `ROUTER_PING`
- `ROUTER_PONG`
- `ROUTER_BUSY`
- `ROUTER_HISTORY`
- `ROUTER_STATS`
- `ROUTER_TEXT_DIRECT`
- `ROUTER_TEXT_BROADCAST`

Client-to-router values:

- `CLIENT_ERROR`
- `CLIENT_HISTORY`
- `CLIENT_STATS`
- `CLIENT_PING`
- `CLIENT_PONG`
- `CLIENT_ABORT`

#### `Statistics`

Important fields:

- `messages_total`
- `messages_saved`
- `messages_max`
- `up_time`
- `requests`
- `requests_history`
- `heartbeat`
- `return_max`
- `return_window`

#### `History`

Important fields:

- `history_messages`
  Number of messages requested or returned.
- `window`
  History window in minutes.
- `last_request`
  Cursor/index for continued history replay.

#### `Heartbeat`

Important fields:

- `period`
  Heartbeat period in seconds.
- `secondary`
  Whether this is a secondary rather than primary server.

The `STORE_FORWARD_APP` plugin implements:

- `CLIENT_PING`
- `CLIENT_STATS`
- `CLIENT_HISTORY`
- `ROUTER_HEARTBEAT`
- replay of archived text as `ROUTER_TEXT_DIRECT` or `ROUTER_TEXT_BROADCAST`

Example client stats request:

```text
rr: CLIENT_STATS
```

Serialized protobuf bytes:

```text
0842
```

Example router heartbeat response:

```text
rr: ROUTER_HEARTBEAT
heartbeat {
  period: 3600
}
```

Serialized protobuf bytes:

```text
0802220308901c
```

Conceptually:

- `rr = CLIENT_STATS`
  The client is asking for store-and-forward statistics
- `rr = ROUTER_HEARTBEAT`
  The router is advertising store-and-forward presence
- `heartbeat.period = 3600`
  Heartbeat period in seconds

Store-forward-related module config lives under `ModuleConfig.store_forward`:

- `enabled`
- `heartbeat`
- `records`
- `history_return_max`
- `history_return_window`
- `is_server`

For the implementation details of the plugin-based host extension, see [meshtastic_plugins.md](./meshtastic_plugins.md).

### `PRIVATE_APP`

`PRIVATE_APP` is only a shared bucket. Meshtastic knows that traffic is “private application traffic,” but it does not know which private sub-protocol you mean inside that bucket.

That means payload discrimination is entirely application-defined.

The proxy supports subtype dispatch:

- JSON payloads with a top-level string `type` field route to `plugins/PRIVATE_APP.<type>.handler.py`
- payloads beginning with `type=<value>` route to `plugins/PRIVATE_APP.<value>.handler.py`
- if no subtype-specific handler exists, the proxy falls back to `plugins/PRIVATE_APP.handler.py`

So `PRIVATE_APP` is closer to “bring your own envelope” than to a fully specified built-in protocol.

A practical pattern is to define a top-level envelope yourself, for example:

```json
{"type":"chat","version":1,"body":"hello"}
```

Then route it to `plugins/PRIVATE_APP.chat.handler.py`.

## Config Structures

### `Config`

`Config` is a oneof over the device-wide config categories:

- `device`
- `position`
- `power`
- `network`
- `display`
- `lora`
- `bluetooth`
- `security`
- `sessionkey`
- `device_ui`

### `ModuleConfig`

`ModuleConfig` is a oneof over module-level config categories:

- `mqtt`
- `serial`
- `external_notification`
- `store_forward`
- `range_test`
- `telemetry`
- `canned_message`
- `audio`
- `remote_hardware`
- `neighbor_info`
- `ambient_lighting`
- `detection_sensor`
- `paxcounter`

This split is why admin reads/writes distinguish `get_config_request` from `get_module_config_request`.

## How This Repo Uses These Protocols

- `meshtastic_messages.py`
  Focuses on text messaging workflows: `TEXT_MESSAGE_APP` and `PRIVATE_APP`.
- `meshtastic_status.py`
  Reads node identity/config state and actively requests `TELEMETRY_APP`.
- `meshtastic_monitor.py`
  Streams live events across multiple packet types.
- `meshtastic_protocol.py`
  Logs broad protocol activity: text, telemetry, node info, routing, and related events.
- `meshtastic_broker.py`
  Watches `ADMIN_APP` because that is where config mutation and control ownership matter.
- `meshtastic_proxy.py` plus plugins
  Dispatches by `portnum`, which makes app-level extension and inspection natural.

## Why This Matters

Understanding Meshtastic as “`MeshPacket` envelope plus `Data.portnum` plus protocol-specific payload” makes the repo much easier to reason about:

- why `messages` ignores some packets and `protocol` logs many more
- why the broker focuses so heavily on `ADMIN_APP`
- why telemetry collection is separate from text-message logging
- why the plugin system dispatches on filenames like `STORE_FORWARD_APP.handler.py`
- why `PRIVATE_APP` needs a subtype convention on top of the built-in port number

Once you think in terms of the envelope and the app payload separately, most of the repo’s boundaries are straightforward.
