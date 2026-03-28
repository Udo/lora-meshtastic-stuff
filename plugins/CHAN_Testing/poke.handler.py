import re


def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return

    if str(event.get("channel_command") or "").strip().lower() != "poke":
        return

    payload = event.get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        return

    text = bytes(payload).decode("utf-8", errors="replace").strip()
    local_short_name = str(event.get("local_short_name") or "").strip()
    command_text = text
    if local_short_name:
        command_text = re.sub(r"^\s*@?%s\s*[:,]?\s*" % re.escape(local_short_name), "", command_text, count=1, flags=re.IGNORECASE)
    if command_text.lower().startswith("poke"):
        command_text = command_text[4:].lstrip(" ,:-")

    response_text = f"poke ack from {local_short_name or 'unknown'}"
    if command_text:
        response_text = f"{response_text}: {command_text}"

    packet = api["mesh_pb2"].MeshPacket()
    packet.to = 0
    packet.channel = int(event.get("packet_channel") or event.get("channel_num") or 0)
    packet.decoded.portnum = api["portnums_pb2"].TEXT_MESSAGE_APP
    packet.decoded.payload = response_text.encode("utf-8")
    packet.decoded.want_response = False
    api["logger"].info("CHAN_Testing/poke replying on channel=%s text=%r", packet.channel, response_text)
    api["send_mesh_packet"](packet)
