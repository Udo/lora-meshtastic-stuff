# Meshtastic `get` / `set` Field Reference

## Purpose

This page documents the field paths you can use with:

```bash
./setup/meshtastic-python.sh get <FIELD>
./setup/meshtastic-python.sh set <FIELD> <VALUE>
```

These wrapper commands map directly to the upstream Meshtastic `--get` and `--set` functionality while preserving the repo's normal target-selection behavior.

The paths below are shown in canonical `snake_case`. The upstream Meshtastic CLI also accepts `camelCase`, but this repo documents the snake_case form.

General rules:

- use leaf fields with `set`, for example `range_test.sender`
- top-level section names can still be useful with `get`, for example `lora` or `telemetry`
- enum fields expect one of the Meshtastic enum names, not a free-form string
- repeated/message fields may be readable with `get`, but are usually not the normal `set` targets

## Examples

```bash
./setup/meshtastic-python.sh get lora.region
./setup/meshtastic-python.sh set lora.region EU_868
./setup/meshtastic-python.sh get range_test.sender
./setup/meshtastic-python.sh set range_test.sender 0
./setup/meshtastic-python.sh get telemetry.environment_update_interval
./setup/meshtastic-python.sh set telemetry.environment_update_interval 900
```

## Device Config

### `device.*`

- `device.role`
- `device.serial_enabled`
- `device.button_gpio`
- `device.buzzer_gpio`
- `device.rebroadcast_mode`
- `device.node_info_broadcast_secs`
- `device.double_tap_as_button_press`
- `device.is_managed`
- `device.disable_triple_click`
- `device.tzdef`
- `device.led_heartbeat_disabled`
- `device.buzzer_mode`

### `position.*`

- `position.position_broadcast_secs`
- `position.position_broadcast_smart_enabled`
- `position.fixed_position`
- `position.gps_enabled`
- `position.gps_update_interval`
- `position.gps_attempt_time`
- `position.position_flags`
- `position.rx_gpio`
- `position.tx_gpio`
- `position.broadcast_smart_minimum_distance`
- `position.broadcast_smart_minimum_interval_secs`
- `position.gps_en_gpio`
- `position.gps_mode`

### `power.*`

- `power.is_power_saving`
- `power.on_battery_shutdown_after_secs`
- `power.adc_multiplier_override`
- `power.wait_bluetooth_secs`
- `power.sds_secs`
- `power.ls_secs`
- `power.min_wake_secs`
- `power.device_battery_ina_address`
- `power.powermon_enables`

### `network.*`

- `network.wifi_enabled`
- `network.wifi_ssid`
- `network.wifi_psk`
- `network.ntp_server`
- `network.eth_enabled`
- `network.address_mode`
- `network.ipv4_config.ip`
- `network.ipv4_config.gateway`
- `network.ipv4_config.subnet`
- `network.ipv4_config.dns`
- `network.rsyslog_server`
- `network.enabled_protocols`
- `network.ipv6_enabled`

### `display.*`

- `display.screen_on_secs`
- `display.gps_format`
- `display.auto_screen_carousel_secs`
- `display.compass_north_top`
- `display.flip_screen`
- `display.units`
- `display.oled`
- `display.displaymode`
- `display.heading_bold`
- `display.wake_on_tap_or_motion`
- `display.compass_orientation`
- `display.use_12h_clock`

### `lora.*`

- `lora.use_preset`
- `lora.modem_preset`
- `lora.bandwidth`
- `lora.spread_factor`
- `lora.coding_rate`
- `lora.frequency_offset`
- `lora.region`
- `lora.hop_limit`
- `lora.tx_enabled`
- `lora.tx_power`
- `lora.channel_num`
- `lora.override_duty_cycle`
- `lora.sx126x_rx_boosted_gain`
- `lora.override_frequency`
- `lora.pa_fan_disabled`
- `lora.ignore_incoming`
- `lora.ignore_mqtt`
- `lora.config_ok_to_mqtt`

### `bluetooth.*`

- `bluetooth.enabled`
- `bluetooth.mode`
- `bluetooth.fixed_pin`

### `security.*`

- `security.public_key`
- `security.private_key`
- `security.admin_key`
- `security.is_managed`
- `security.serial_enabled`
- `security.debug_log_api_enabled`
- `security.admin_channel_enabled`

### `sessionkey.*`

- `sessionkey`

### `device_ui.*`

- `device_ui.version`
- `device_ui.screen_brightness`
- `device_ui.screen_timeout`
- `device_ui.screen_lock`
- `device_ui.settings_lock`
- `device_ui.pin_code`
- `device_ui.theme`
- `device_ui.alert_enabled`
- `device_ui.banner_enabled`
- `device_ui.ring_tone_id`
- `device_ui.language`
- `device_ui.node_filter.unknown_switch`
- `device_ui.node_filter.offline_switch`
- `device_ui.node_filter.public_key_switch`
- `device_ui.node_filter.hops_away`
- `device_ui.node_filter.position_switch`
- `device_ui.node_filter.node_name`
- `device_ui.node_filter.channel`
- `device_ui.node_highlight.chat_switch`
- `device_ui.node_highlight.position_switch`
- `device_ui.node_highlight.telemetry_switch`
- `device_ui.node_highlight.iaq_switch`
- `device_ui.node_highlight.node_name`
- `device_ui.calibration_data`
- `device_ui.map_data.home.zoom`
- `device_ui.map_data.home.latitude`
- `device_ui.map_data.home.longitude`
- `device_ui.map_data.style`
- `device_ui.map_data.follow_gps`

## Module Config

### `mqtt.*`

- `mqtt.enabled`
- `mqtt.address`
- `mqtt.username`
- `mqtt.password`
- `mqtt.encryption_enabled`
- `mqtt.json_enabled`
- `mqtt.tls_enabled`
- `mqtt.root`
- `mqtt.proxy_to_client_enabled`
- `mqtt.map_reporting_enabled`
- `mqtt.map_report_settings.publish_interval_secs`
- `mqtt.map_report_settings.position_precision`
- `mqtt.map_report_settings.should_report_location`

### `serial.*`

- `serial.enabled`
- `serial.echo`
- `serial.rxd`
- `serial.txd`
- `serial.baud`
- `serial.timeout`
- `serial.mode`
- `serial.override_console_serial_port`

### `external_notification.*`

- `external_notification.enabled`
- `external_notification.output_ms`
- `external_notification.output`
- `external_notification.output_vibra`
- `external_notification.output_buzzer`
- `external_notification.active`
- `external_notification.alert_message`
- `external_notification.alert_message_vibra`
- `external_notification.alert_message_buzzer`
- `external_notification.alert_bell`
- `external_notification.alert_bell_vibra`
- `external_notification.alert_bell_buzzer`
- `external_notification.use_pwm`
- `external_notification.nag_timeout`
- `external_notification.use_i2s_as_buzzer`

### `store_forward.*`

- `store_forward.enabled`
- `store_forward.heartbeat`
- `store_forward.records`
- `store_forward.history_return_max`
- `store_forward.history_return_window`
- `store_forward.is_server`

### `range_test.*`

- `range_test.enabled`
- `range_test.sender`
- `range_test.save`

### `telemetry.*`

- `telemetry.device_update_interval`
- `telemetry.environment_update_interval`
- `telemetry.environment_measurement_enabled`
- `telemetry.environment_screen_enabled`
- `telemetry.environment_display_fahrenheit`
- `telemetry.air_quality_enabled`
- `telemetry.air_quality_interval`
- `telemetry.power_measurement_enabled`
- `telemetry.power_update_interval`
- `telemetry.power_screen_enabled`
- `telemetry.health_measurement_enabled`
- `telemetry.health_update_interval`
- `telemetry.health_screen_enabled`

### `canned_message.*`

- `canned_message.rotary1_enabled`
- `canned_message.inputbroker_pin_a`
- `canned_message.inputbroker_pin_b`
- `canned_message.inputbroker_pin_press`
- `canned_message.inputbroker_event_cw`
- `canned_message.inputbroker_event_ccw`
- `canned_message.inputbroker_event_press`
- `canned_message.updown1_enabled`
- `canned_message.enabled`
- `canned_message.allow_input_source`
- `canned_message.send_bell`

### `audio.*`

- `audio.codec2_enabled`
- `audio.ptt_pin`
- `audio.bitrate`
- `audio.i2s_ws`
- `audio.i2s_sd`
- `audio.i2s_din`
- `audio.i2s_sck`

### `remote_hardware.*`

- `remote_hardware.enabled`
- `remote_hardware.allow_undefined_pin_access`
- `remote_hardware.available_pins`

Note:
- `remote_hardware.available_pins` is a repeated message-style structure, not a typical scalar `set` target.

### `neighbor_info.*`

- `neighbor_info.enabled`
- `neighbor_info.update_interval`
- `neighbor_info.transmit_over_lora`

### `ambient_lighting.*`

- `ambient_lighting.led_state`
- `ambient_lighting.current`
- `ambient_lighting.red`
- `ambient_lighting.green`
- `ambient_lighting.blue`

### `detection_sensor.*`

- `detection_sensor.enabled`
- `detection_sensor.minimum_broadcast_secs`
- `detection_sensor.state_broadcast_secs`
- `detection_sensor.send_bell`
- `detection_sensor.name`
- `detection_sensor.monitor_pin`
- `detection_sensor.detection_trigger_type`
- `detection_sensor.use_pullup`

### `paxcounter.*`

- `paxcounter.enabled`
- `paxcounter.paxcounter_update_interval`
- `paxcounter.wifi_threshold`
- `paxcounter.ble_threshold`

## Notes

- Enum fields such as `device.role`, `lora.region`, `lora.modem_preset`, `bluetooth.mode`, and `range_test.enabled` must use the corresponding Meshtastic enum names or scalar values expected by the upstream CLI.
- Byte fields such as `security.public_key`, `security.private_key`, and `security.admin_key` are not normal day-to-day settings and should be changed with care.
- If you need to discover what the installed Meshtastic CLI currently accepts on your machine, the upstream CLI also supports:

```bash
./.venv/bin/python -m meshtastic --get 0
```

That asks the Meshtastic CLI itself to print its current field inventory.
