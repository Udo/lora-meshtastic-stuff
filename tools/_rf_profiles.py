#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Marker:
    label: str
    freq_hz: int


@dataclass(frozen=True)
class Profile:
    center_hz: int
    span_hz: int
    marker_set: str
    description: str


EU868_LOW_MARKERS = [
    Marker("867.1", 867_100_000),
    Marker("867.3", 867_300_000),
    Marker("867.5", 867_500_000),
    Marker("867.7", 867_700_000),
    Marker("867.9", 867_900_000),
    Marker("868.1", 868_100_000),
    Marker("868.3", 868_300_000),
    Marker("868.5", 868_500_000),
]

EU868_HIGH_MARKERS = [
    Marker("869.525", 869_525_000),
    Marker("869.850", 869_850_000),
]

EU868_WIDE_MARKERS = EU868_LOW_MARKERS + EU868_HIGH_MARKERS

ISM433_MARKERS = [
    Marker("433.050", 433_050_000),
    Marker("433.920", 433_920_000),
    Marker("434.790", 434_790_000),
]

PMR446_MARKERS = [
    Marker("446.006", 446_006_250),
    Marker("446.094", 446_093_750),
    Marker("446.194", 446_193_750),
]

AIRBAND_MARKERS = [
    Marker("131.525", 131_525_000),
    Marker("131.725", 131_725_000),
    Marker("131.825", 131_825_000),
    Marker("121.500", 121_500_000),
]

ADSB1090_MARKERS = [
    Marker("1090.000", 1_090_000_000),
]

APRS_MARKERS = [
    Marker("144.390", 144_390_000),
    Marker("144.800", 144_800_000),
    Marker("145.825", 145_825_000),
]

AIS_MARKERS = [
    Marker("156.800", 156_800_000),
    Marker("161.975", 161_975_000),
    Marker("162.025", 162_025_000),
]

ACARS_MARKERS = [
    Marker("131.525", 131_525_000),
    Marker("131.550", 131_550_000),
    Marker("131.725", 131_725_000),
    Marker("131.825", 131_825_000),
    Marker("131.850", 131_850_000),
]

RTL433_433_MARKERS = ISM433_MARKERS

RTL433_868_MARKERS = [
    Marker("868.100", 868_100_000),
    Marker("868.300", 868_300_000),
    Marker("868.500", 868_500_000),
    Marker("868.950", 868_950_000),
    Marker("869.525", 869_525_000),
]

RTL433_915_MARKERS = [
    Marker("914.900", 914_900_000),
    Marker("915.000", 915_000_000),
    Marker("915.200", 915_200_000),
]

AM_BROADCAST_MARKERS = [
    Marker("540", 540_000),
    Marker("720", 720_000),
    Marker("900", 900_000),
    Marker("1080", 1_080_000),
    Marker("1260", 1_260_000),
    Marker("1440", 1_440_000),
    Marker("1600", 1_600_000),
]

SHORTWAVE_MARKERS = [
    Marker("5.0", 5_000_000),
    Marker("7.1", 7_100_000),
    Marker("9.5", 9_500_000),
    Marker("11.7", 11_700_000),
    Marker("13.8", 13_800_000),
]

NOAA_WEATHER_MARKERS = [
    Marker("162.400", 162_400_000),
    Marker("162.425", 162_425_000),
    Marker("162.450", 162_450_000),
    Marker("162.475", 162_475_000),
    Marker("162.500", 162_500_000),
    Marker("162.525", 162_525_000),
    Marker("162.550", 162_550_000),
]

MARINE_VHF_MARKERS = [
    Marker("156.800", 156_800_000),
    Marker("156.300", 156_300_000),
    Marker("157.100", 157_100_000),
]

CB27_MARKERS = [
    Marker("26.965", 26_965_000),
    Marker("27.185", 27_185_000),
    Marker("27.405", 27_405_000),
]

HAM_10M_MARKERS = [
    Marker("28.400", 28_400_000),
    Marker("29.000", 29_000_000),
    Marker("29.600", 29_600_000),
]

HAM_6M_MARKERS = [
    Marker("50.125", 50_125_000),
    Marker("52.525", 52_525_000),
]

HAM_2M_MARKERS = [
    Marker("144.390", 144_390_000),
    Marker("144.800", 144_800_000),
    Marker("145.500", 145_500_000),
    Marker("146.520", 146_520_000),
]

MURS_MARKERS = [
    Marker("151.820", 151_820_000),
    Marker("151.880", 151_880_000),
    Marker("151.940", 151_940_000),
    Marker("154.570", 154_570_000),
    Marker("154.600", 154_600_000),
]

HAM_70CM_MARKERS = [
    Marker("433.000", 433_000_000),
    Marker("433.920", 433_920_000),
    Marker("446.000", 446_000_000),
]

FRS_GMRS_MARKERS = [
    Marker("462.5625", 462_562_500),
    Marker("462.6750", 462_675_000),
    Marker("462.7250", 462_725_000),
    Marker("467.5625", 467_562_500),
    Marker("467.7125", 467_712_500),
]

ISM915_MARKERS = [
    Marker("902.300", 902_300_000),
    Marker("915.000", 915_000_000),
    Marker("927.500", 927_500_000),
]

RDS_MARKERS = [
    Marker("87.6", 87_600_000),
    Marker("89.4", 89_400_000),
    Marker("95.8", 95_800_000),
    Marker("100.5", 100_500_000),
    Marker("104.6", 104_600_000),
]

VDL2_MARKERS = [
    Marker("136.650", 136_650_000),
    Marker("136.725", 136_725_000),
    Marker("136.775", 136_775_000),
    Marker("136.825", 136_825_000),
    Marker("136.875", 136_875_000),
    Marker("136.975", 136_975_000),
]

PAGER_MARKERS = [
    Marker("148.600", 148_600_000),
    Marker("149.000", 149_000_000),
    Marker("153.350", 153_350_000),
    Marker("169.650", 169_650_000),
]

NOAA_APT_MARKERS = [
    Marker("NOAA15", 137_620_000),
    Marker("NOAA18", 137_912_500),
    Marker("NOAA19", 137_100_000),
]

LORA_EU868_MARKERS = [
    Marker("868.100", 868_100_000),
    Marker("868.300", 868_300_000),
    Marker("868.500", 868_500_000),
    Marker("869.525", 869_525_000),
]


MARKER_SETS = {
    "none": [],
    "max-span": [],
    "eu868-low": EU868_LOW_MARKERS,
    "eu868-high": EU868_HIGH_MARKERS,
    "eu868-wide": EU868_WIDE_MARKERS,
    "am-broadcast": AM_BROADCAST_MARKERS,
    "shortwave": SHORTWAVE_MARKERS,
    "weather": NOAA_WEATHER_MARKERS,
    "marine-vhf": MARINE_VHF_MARKERS,
    "cb-27mhz": CB27_MARKERS,
    "ham-10m": HAM_10M_MARKERS,
    "ham-6m": HAM_6M_MARKERS,
    "ham-2m": HAM_2M_MARKERS,
    "murs": MURS_MARKERS,
    "ism433": ISM433_MARKERS,
    "pmr446": PMR446_MARKERS,
    "ham-70cm": HAM_70CM_MARKERS,
    "frs-gmrs": FRS_GMRS_MARKERS,
    "ism915": ISM915_MARKERS,
    "airband": AIRBAND_MARKERS,
    "adsb1090": ADSB1090_MARKERS,
    "adsb-monitor": ADSB1090_MARKERS,
    "aprs": APRS_MARKERS,
    "aprs-monitor": APRS_MARKERS,
    "ais": AIS_MARKERS,
    "ais-monitor": AIS_MARKERS,
    "acars": ACARS_MARKERS,
    "acars-monitor": ACARS_MARKERS,
    "weather-alert": NOAA_WEATHER_MARKERS,
    "weather-alert-monitor": NOAA_WEATHER_MARKERS,
    "rds": RDS_MARKERS,
    "rds-monitor": RDS_MARKERS,
    "vdl2": VDL2_MARKERS,
    "vdl2-monitor": VDL2_MARKERS,
    "pager": PAGER_MARKERS,
    "pager-monitor": PAGER_MARKERS,
    "noaa-apt": NOAA_APT_MARKERS,
    "noaa-apt-monitor": NOAA_APT_MARKERS,
    "lora-eu868": LORA_EU868_MARKERS,
    "lora-monitor": LORA_EU868_MARKERS,
    "rtl433-433": RTL433_433_MARKERS,
    "rtl433-868": RTL433_868_MARKERS,
    "rtl433-915": RTL433_915_MARKERS,
    "fm-broadcast": [],
    "broadband-868": [],
}


WATERFALL_PROFILES = {
    "max-span": Profile(868_475_000, 3_200_000, "none", "Maximum instantaneous span supported by this workflow"),
    "eu868-wide": Profile(868_475_000, 3_200_000, "eu868-wide", "Wide EU868 overview"),
    "eu868-low": Profile(867_900_000, 2_048_000, "eu868-low", "Lower EU868 LoRa channels"),
    "eu868-high": Profile(869_525_000, 2_048_000, "eu868-high", "Upper EU868 / Meshtastic-ish area"),
    "am-broadcast": Profile(1_050_000, 1_600_000, "am-broadcast", "AM broadcast band"),
    "shortwave-49m": Profile(6_100_000, 2_400_000, "shortwave", "Shortwave 49m-ish listening window"),
    "shortwave-31m": Profile(9_650_000, 2_400_000, "shortwave", "Shortwave 31m-ish listening window"),
    "weather": Profile(162_475_000, 400_000, "weather", "NOAA weather radio"),
    "marine-vhf": Profile(156_800_000, 2_400_000, "marine-vhf", "Marine VHF"),
    "cb-27mhz": Profile(27_185_000, 2_400_000, "cb-27mhz", "11m CB radio"),
    "ham-10m": Profile(28_850_000, 3_200_000, "ham-10m", "10 meter amateur band"),
    "ham-6m": Profile(51_000_000, 2_400_000, "ham-6m", "6 meter amateur band"),
    "ham-2m": Profile(146_000_000, 4_000_000, "ham-2m", "2 meter amateur band"),
    "murs": Profile(152_500_000, 4_000_000, "murs", "US MURS channels"),
    "ism433": Profile(433_920_000, 2_400_000, "ism433", "433 MHz ISM devices"),
    "pmr446": Profile(446_100_000, 2_400_000, "pmr446", "PMR446 handheld channels"),
    "ham-70cm": Profile(434_500_000, 4_000_000, "ham-70cm", "70cm amateur / LPD-ish region"),
    "frs-gmrs": Profile(465_137_500, 6_000_000, "frs-gmrs", "FRS / GMRS handheld channels"),
    "ism915": Profile(915_000_000, 12_000_000, "ism915", "902-928 MHz ISM band slice"),
    "airband": Profile(121_500_000, 2_400_000, "airband", "Airband around 121.5 MHz"),
    "adsb1090": Profile(1_090_000_000, 2_400_000, "adsb1090", "ADS-B / Mode S at 1090 MHz"),
    "adsb-monitor": Profile(1_090_000_000, 2_400_000, "adsb-monitor", "ADS-B / Mode S monitor band"),
    "aprs": Profile(144_800_000, 2_400_000, "aprs", "APRS around 144.800 MHz"),
    "aprs-monitor": Profile(144_800_000, 2_400_000, "aprs-monitor", "APRS monitor band"),
    "ais": Profile(162_000_000, 1_200_000, "ais", "AIS marine channels"),
    "ais-monitor": Profile(162_000_000, 1_200_000, "ais-monitor", "AIS marine monitor band"),
    "acars": Profile(131_700_000, 2_400_000, "acars", "ACARS VHF channels"),
    "acars-monitor": Profile(131_700_000, 2_400_000, "acars-monitor", "ACARS monitor band"),
    "weather-alert": Profile(162_475_000, 400_000, "weather-alert", "Weather alert channels"),
    "weather-alert-monitor": Profile(162_475_000, 400_000, "weather-alert-monitor", "Weather alert monitor band"),
    "rds": Profile(100_500_000, 3_200_000, "rds", "FM broadcast / RDS window"),
    "rds-monitor": Profile(100_500_000, 3_200_000, "rds-monitor", "FM broadcast / RDS monitor band"),
    "vdl2": Profile(136_800_000, 1_600_000, "vdl2", "VDL2 aviation data channels"),
    "vdl2-monitor": Profile(136_800_000, 1_600_000, "vdl2-monitor", "VDL2 aviation monitor band"),
    "pager": Profile(169_650_000, 2_400_000, "pager", "Common pager channels"),
    "pager-monitor": Profile(169_650_000, 2_400_000, "pager-monitor", "Pager monitor band"),
    "noaa-apt": Profile(137_500_000, 1_600_000, "noaa-apt", "NOAA APT weather satellite band"),
    "noaa-apt-monitor": Profile(137_500_000, 1_600_000, "noaa-apt-monitor", "NOAA APT monitor band"),
    "lora-eu868": Profile(868_300_000, 1_000_000, "lora-eu868", "Focused EU868 LoRa channel"),
    "lora-monitor": Profile(868_300_000, 1_000_000, "lora-monitor", "LoRa monitor band"),
    "rtl433-433": Profile(433_920_000, 2_400_000, "rtl433-433", "rtl_433 433 MHz sensor band"),
    "rtl433-868": Profile(868_300_000, 2_048_000, "rtl433-868", "rtl_433 868 MHz sensor band"),
    "rtl433-915": Profile(915_000_000, 2_400_000, "rtl433-915", "rtl_433 915 MHz sensor band"),
    "fm-broadcast": Profile(100_500_000, 3_200_000, "none", "Wide FM broadcast slice"),
    "broadband-868": Profile(868_475_000, 3_200_000, "none", "Max-span EU868 broadband view"),
}


RTL433_PRESETS = {
    "433": {"frequency": 433.92, "sample_rate": 250000, "description": "Common 433 MHz ISM sensors"},
    "868": {"frequency": 868.30, "sample_rate": 250000, "description": "Common EU 868 MHz ISM sensors"},
    "915": {"frequency": 915.00, "sample_rate": 250000, "description": "Common 915 MHz ISM sensors"},
}
