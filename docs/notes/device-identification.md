# Device Identification

- Physical device behavior and `esptool` output match Meshnology N35 / Heltec WiFi LoRa 32 V3 class hardware.
- Confirmed traits:
  - ESP32-S3
  - SX1262-class LoRa board family
  - CP2102 USB to serial bridge
  - 8 MB embedded flash
  - 0.96 inch OLED class display
- Original factory image was not responding to the Meshtastic serial protocol.
- Original flash string scan showed Heltec demo/test firmware markers such as `heltec_test`, `LoRa Test XX`, and `HELTEC_WIFI_LORA_32_V3`.
- The board now runs Meshtastic `2.7.13.597fa0b` with `pioEnv=heltec-v3` and responds normally over USB.

# Implications

- The screen turning off during serial access is consistent with a normal ESP32 reset caused by RTS/DTR changes on CP2102-based boards.
- The original image was a Heltec WiFi/LoRa demo image rather than Meshtastic.
- Meshtastic `2.7.15` bootlooped on this board after a clean flash.
- Meshtastic `2.7.13` is currently the known-good baseline for this device in this workspace.

# Target Connection Model

- USB from a computer:
  - Meshtastic CLI over serial
  - Meshtastic Web client over Web Serial
- Phone access:
  - Android app over WiFi on the same network
  - WiFi on ESP32 nodes disables Bluetooth in Meshtastic, so WiFi and BLE are mutually exclusive
