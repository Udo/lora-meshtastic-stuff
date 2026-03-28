def handle_packet(event, api):
    if event.get("plugin_origin_likely"):
        return

    payload = event.get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        return

    text = bytes(payload).decode("utf-8", errors="replace").strip()
    # Strip leading "ping" command word (case-insensitive)
    rest = text[4:].lstrip(" ,:-") if text.lower().startswith("ping") else text

    local_short_name = str(event.get("local_short_name") or "").strip()
    sender = str(event.get("sender_short_name") or "").strip()
    reply = f"pong from {local_short_name or 'unknown'}"
    if rest:
        reply = f"{reply}: {rest}"

    packet = api["mesh_pb2"].MeshPacket()
    packet.to = int(event.get("packet_from") or 0)
    packet.channel = 0
    packet.decoded.portnum = api["portnums_pb2"].TEXT_MESSAGE_APP
    packet.decoded.payload = reply.encode("utf-8")
    packet.decoded.want_response = False
    api["logger"].info("DM/ping replying to %s: %r", sender or packet.to, reply)
    api["send_mesh_packet"](packet)
