import json
import re
import time
from pathlib import Path


MAX_DM_REPLY_CHARS = 180
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")


def _repo_root(api) -> Path:
    return Path(api["plugin_path"]).resolve().parents[2]


def bbs_dir(api) -> Path:
    return _repo_root(api) / "data" / "bbs"


def users_dir(api) -> Path:
    path = bbs_dir(api) / "users"
    path.mkdir(parents=True, exist_ok=True)
    return path


def topics_dir(api) -> Path:
    path = bbs_dir(api) / "topics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def posts_dir(api) -> Path:
    path = bbs_dir(api) / "posts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sender_key(event) -> str | None:
    packet_from = int(event.get("packet_from") or 0)
    if packet_from <= 0:
        return None
    return str(packet_from)


def sender_label(event) -> str:
    short_name = str(event.get("sender_short_name") or "").strip()
    if short_name:
        return short_name
    packet_from = int(event.get("packet_from") or 0)
    return f"!{packet_from:08x}" if packet_from > 0 else "unknown"


def now_ts() -> int:
    return int(time.time())


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def send_text_reply(event, api, text: str) -> None:
    packet_from = int(event.get("packet_from") or 0)
    if packet_from <= 0:
        return
    safe_text = text.strip()
    if len(safe_text) > MAX_DM_REPLY_CHARS:
        safe_text = safe_text[: MAX_DM_REPLY_CHARS - 3].rstrip() + "..."
    packet = api["mesh_pb2"].MeshPacket()
    packet.to = packet_from
    packet.channel = 0
    packet.decoded.portnum = api["portnums_pb2"].TEXT_MESSAGE_APP
    packet.decoded.payload = safe_text.encode("utf-8")
    packet.decoded.want_response = False
    api["send_mesh_packet"](packet)


def user_path(api, sender_id: str) -> Path:
    return users_dir(api) / f"{sender_id}.json"


def load_user(event, api) -> dict:
    sender_id = sender_key(event)
    if sender_id is None:
        return {}
    path = user_path(api, sender_id)
    record = read_json(path, {})
    now = now_ts()
    record.update(
        {
            "first_seen_at": int(record.get("first_seen_at") or now),
            "last_seen_at": now,
            "node_num": int(event.get("packet_from") or 0),
            "sender_short_name": str(event.get("sender_short_name") or "").strip() or None,
            "subscriptions": sorted({str(item) for item in record.get("subscriptions") or [] if str(item).strip()}),
        }
    )
    return record


def upsert_user(event, api) -> dict:
    sender_id = sender_key(event)
    if sender_id is None:
        return {}
    record = load_user(event, api)
    write_json(user_path(api, sender_id), record)
    return record


def record_first_contact(event, api) -> bool:
    sender_id = sender_key(event)
    if sender_id is None:
        return False
    path = user_path(api, sender_id)
    if path.exists():
        upsert_user(event, api)
        return False
    write_json(path, load_user(event, api))
    return True


def all_topics(api) -> list[dict]:
    items = []
    for path in sorted(topics_dir(api).glob("*.json")):
        topic = read_json(path, {})
        name = str(topic.get("name") or path.stem).strip().lower()
        if not name:
            continue
        topic["name"] = name
        topic["post_count"] = int(topic.get("post_count") or 0)
        topic["updated_at"] = int(topic.get("updated_at") or 0)
        items.append(topic)
    items.sort(key=lambda item: (-int(item.get("updated_at") or 0), item["name"]))
    return items


def topic_path(api, topic: str) -> Path:
    return topics_dir(api) / f"{topic}.json"


def normalize_topic(raw: str) -> str | None:
    topic = raw.strip().lower()
    if not TOPIC_RE.fullmatch(topic):
        return None
    return topic


def load_topic(api, topic: str) -> dict:
    return read_json(topic_path(api, topic), {"name": topic, "created_at": now_ts(), "updated_at": 0, "post_count": 0})


def next_post_id(api) -> int:
    max_id = 0
    for path in posts_dir(api).glob("*.json"):
        try:
            max_id = max(max_id, int(path.stem))
        except ValueError:
            continue
    return max_id + 1


def create_post(event, api, topic: str, text: str) -> dict:
    post_id = next_post_id(api)
    created_at = now_ts()
    post = {
        "id": post_id,
        "topic": topic,
        "text": text.strip(),
        "author": sender_label(event),
        "author_node_num": int(event.get("packet_from") or 0),
        "created_at": created_at,
    }
    write_json(posts_dir(api) / f"{post_id}.json", post)

    topic_record = load_topic(api, topic)
    topic_record.update(
        {
            "name": topic,
            "updated_at": created_at,
            "post_count": int(topic_record.get("post_count") or 0) + 1,
            "last_post_id": post_id,
        }
    )
    write_json(topic_path(api, topic), topic_record)
    return post


def all_posts(api) -> list[dict]:
    items = []
    for path in sorted(posts_dir(api).glob("*.json")):
        post = read_json(path, {})
        if not post:
            continue
        try:
            post["id"] = int(post.get("id") or path.stem)
        except ValueError:
            continue
        post["created_at"] = int(post.get("created_at") or 0)
        items.append(post)
    items.sort(key=lambda item: (-item["id"], -item["created_at"]))
    return items


def posts_for_topic(api, topic: str, *, limit: int = 3) -> list[dict]:
    return [post for post in all_posts(api) if str(post.get("topic")) == topic][:limit]


def post_by_id(api, post_id: int) -> dict | None:
    path = posts_dir(api) / f"{post_id}.json"
    if not path.exists():
        return None
    post = read_json(path, None)
    if isinstance(post, dict):
        return post
    return None


def save_user_record(api, sender_id: str, record: dict) -> None:
    write_json(user_path(api, sender_id), record)


def banner_text(event, api) -> str:
    bbs_name = str(event.get("local_short_name") or "").strip() or "BBS"
    user_count = len(list(users_dir(api).glob("*.json")))
    topic_count = len(all_topics(api))
    return "\n".join(
        [
            f"{bbs_name} BBS",
            f"Status: online | users: {user_count} | topics: {topic_count}",
            f"Caller: {sender_label(event)}",
            "Cmds: hello help list news put show sub unsub time topics who",
        ]
    )


def command_text(event) -> str:
    payload = event.get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        return ""
    return bytes(payload).decode("utf-8", errors="replace").strip()


def handle_command(event, api) -> str:
    sender_id = sender_key(event)
    if sender_id is None:
        return "Missing sender id."

    text = command_text(event)
    if not text:
        return banner_text(event, api)

    user = upsert_user(event, api)
    parts = text.split(maxsplit=2)
    command = parts[0].lower()

    if command in {"hello", "help"}:
        return banner_text(event, api)
    if command in {"list", "topics"}:
        topics = all_topics(api)[:5]
        if not topics:
            return "Topics: none yet. Use put <topic> <text>."
        rendered = ", ".join(f"{item['name']}({item['post_count']})" for item in topics)
        return f"Topics: {rendered}"
    if command == "who":
        user_records = []
        for path in users_dir(api).glob("*.json"):
            record = read_json(path, {})
            if not isinstance(record, dict):
                continue
            label = str(record.get("sender_short_name") or f"!{int(record.get('node_num') or 0):08x}")
            user_records.append((int(record.get("last_seen_at") or 0), label))
        if not user_records:
            return "Users: none"
        user_records.sort(reverse=True)
        return "Users: " + ", ".join(label for _, label in user_records[:5])
    if command == "time":
        return time.strftime("Time: %Y-%m-%d %H:%M:%S UTC", time.gmtime())
    if command == "put":
        if len(parts) < 3:
            return "Usage: put <topic> <text>"
        topic = normalize_topic(parts[1])
        if topic is None:
            return "Topic must match [a-z0-9_-], max 24 chars."
        body = parts[2].strip()
        if not body:
            return "Usage: put <topic> <text>"
        post = create_post(event, api, topic, body[:120])
        return f"Posted #{post['id']} to {topic}. Use show {topic}."
    if command == "show":
        if len(parts) < 2:
            posts = all_posts(api)[:3]
        else:
            if parts[1].isdigit():
                post = post_by_id(api, int(parts[1]))
                posts = [post] if post else []
            else:
                topic = normalize_topic(parts[1])
                posts = posts_for_topic(api, topic, limit=3) if topic else []
        if not posts:
            return "No posts found."
        lines = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = int(post.get("id") or 0)
            topic = str(post.get("topic") or "?")
            author = str(post.get("author") or "?")
            body = str(post.get("text") or "").strip().replace("\n", " ")
            lines.append(f"#{post_id} {topic} {author}: {body[:44]}")
        return "\n".join(lines[:3])
    if command == "sub":
        if len(parts) < 2:
            return "Usage: sub <topic>"
        topic = normalize_topic(parts[1])
        if topic is None:
            return "Usage: sub <topic>"
        subscriptions = set(user.get("subscriptions") or [])
        subscriptions.add(topic)
        user["subscriptions"] = sorted(subscriptions)
        save_user_record(api, sender_id, user)
        return f"Subscribed: {topic}"
    if command == "unsub":
        if len(parts) < 2:
            return "Usage: unsub <topic>"
        topic = normalize_topic(parts[1])
        if topic is None:
            return "Usage: unsub <topic>"
        subscriptions = set(user.get("subscriptions") or [])
        subscriptions.discard(topic)
        user["subscriptions"] = sorted(subscriptions)
        save_user_record(api, sender_id, user)
        return f"Subscriptions: {', '.join(user['subscriptions']) or 'none'}"
    if command == "news":
        subscriptions = [str(item) for item in user.get("subscriptions") or []]
        posts = all_posts(api)
        if subscriptions:
            posts = [post for post in posts if str(post.get("topic")) in subscriptions]
        posts = posts[:3]
        if not posts:
            return "News: none yet."
        lines = [f"News for {','.join(subscriptions) if subscriptions else 'all'}:"]
        for post in posts:
            lines.append(f"#{post['id']} {post['topic']}: {str(post.get('text') or '')[:32]}")
        return "\n".join(lines)
    return "Unknown command. Try: hello, list, put, show, sub, news, who"
