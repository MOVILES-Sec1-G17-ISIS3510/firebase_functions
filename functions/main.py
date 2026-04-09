import re
import unicodedata

from firebase_admin import firestore, initialize_app, messaging
from firebase_functions.firestore_fn import DocumentSnapshot, Event, on_document_created
from firebase_functions.options import set_global_options

set_global_options(max_instances=10)
initialize_app()

SPORT_SPLIT_PATTERN = re.compile(r"[,;|\n/]+")


def normalize_sport(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    no_accents = "".join(
        ch
        for ch in unicodedata.normalize("NFD", raw)
        if unicodedata.category(ch) != "Mn"
    )
    clean = " ".join(no_accents.split())

    aliases = {
        "soccer": "soccer",
        "football": "soccer",
        "futbol": "soccer",
        "basket": "basketball",
        "basketball": "basketball",
        "baloncesto": "basketball",
        "tenis": "tennis",
        "tennis": "tennis",
        "running": "running",
        "correr": "running",
        "calistenia": "calisthenics",
        "calisthenics": "calisthenics",
        "calistennics": "calisthenics",
        "other": "other",
    }

    return aliases.get(clean, clean)


def parse_user_sports(value: object) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, (list, tuple, set)):
        raw_values = [str(item) for item in value]
    else:
        raw_values = SPORT_SPLIT_PATTERN.split(str(value))

    sports: set[str] = set()
    for raw_value in raw_values:
        sport = normalize_sport(raw_value)
        if sport:
            sports.add(sport)

    return sports


def collect_tokens_for_user_ids(db: firestore.Client, user_ids: set[str]) -> set[str]:
    if not user_ids:
        return set()

    user_refs = [db.collection("users").document(uid) for uid in user_ids]
    user_docs = db.get_all(user_refs)

    tokens: set[str] = set()
    for user_doc in user_docs:
        if not user_doc.exists:
            continue

        user_data = user_doc.to_dict() or {}
        fcm_tokens = user_data.get("fcmTokens")
        if isinstance(fcm_tokens, list):
            for token in fcm_tokens:
                if isinstance(token, str) and token.strip():
                    tokens.add(token.strip())
        elif isinstance(user_data.get("fcmToken"), str):
            token = user_data.get("fcmToken", "").strip()
            if token:
                tokens.add(token)

    return tokens


def resolve_user_display_name(user_data: dict) -> str:
    if not isinstance(user_data, dict):
        return "Someone"

    for key in ["displayName", "name", "fullName", "username"]:
        value = str(user_data.get(key) or "").strip()
        if value:
            return value

    first_name = str(user_data.get("firstName") or "").strip()
    last_name = str(user_data.get("lastName") or "").strip()
    full = " ".join(part for part in [first_name, last_name] if part)
    if full:
        return full

    return "Someone"


def send_notification_to_tokens(
    tokens: set[str],
    title: str,
    body: str,
    data: dict[str, str],
) -> None:
    if not tokens:
        return

    multicast = messaging.MulticastMessage(
        tokens=list(tokens),
        data={
            **data,
            "title": title,
            "body": body,
        },
        android=messaging.AndroidConfig(
            priority="high",
        ),
    )

    result = messaging.send_each_for_multicast(multicast)
    print(
        f"send_notification_to_tokens: sent={result.success_count}, failed={result.failure_count}, recipients={len(tokens)}"
    )


@on_document_created(
    document="communities/{communityId}/channels/{channelId}/messages/{messageId}"
)
def notify_community_message(event: Event[DocumentSnapshot | None]) -> None:
    if event.data is None:
        print("notify_community_message: event has no data")
        return

    payload = event.data.to_dict() or {}
    community_id = event.params.get("communityId", "")
    channel_id = event.params.get("channelId", "")
    message_id = event.params.get("messageId", "")

    if not community_id:
        print("notify_community_message: missing community_id")
        return
    author_id = payload.get("authorId") or ""

    author_name = payload.get("authorName") or "Someone"
    content = payload.get("content") or "New message"

    db = firestore.client()
    members_stream = (
        db.collection("communities")
        .document(community_id)
        .collection("members")
        .stream()
    )

    member_user_ids: set[str] = set()
    for doc in members_stream:
        member_data = doc.to_dict() or {}
        uid = member_data.get("userId") or doc.id
        if uid and uid != author_id:
            member_user_ids.add(str(uid))

    if not member_user_ids:
        print(
            f"notify_community_message: no recipient members after excluding author. "
            f"community={community_id}, author={author_id}"
        )
        return

    tokens = collect_tokens_for_user_ids(db, member_user_ids)

    if not tokens:
        print(
            f"notify_community_message: no tokens found for recipients. "
            f"community={community_id}, recipients={len(member_user_ids)}"
        )
        return

    send_notification_to_tokens(
        tokens=tokens,
        title="Nuevo mensaje en comunidad",
        body=f"{author_name}: {content}",
        data={
            "type": "community_message",
            "authorId": str(author_id),
            "communityId": str(community_id),
            "channelId": str(channel_id),
            "messageId": str(message_id),
            "title": "Nuevo mensaje en comunidad",
            "body": f"{author_name}: {content}",
        },
    )

    print(
        f"notify_community_message: done community={community_id}, channel={channel_id}, message={message_id}, recipients={len(tokens)}"
    )


@on_document_created(document="events/{eventId}")
def notify_open_match_by_sport(event: Event[DocumentSnapshot | None]) -> None:
    if event.data is None:
        print("notify_open_match_by_sport: event has no data")
        return

    payload = event.data.to_dict() or {}
    event_id = event.params.get("eventId", "")
    if not event_id:
        print("notify_open_match_by_sport: missing event_id")
        return

    status = (payload.get("status") or "").strip().lower()
    if status and status != "active":
        print(
            f"notify_open_match_by_sport: skip non-active event {event_id} status={status}"
        )
        return

    sport = normalize_sport(str(payload.get("sport") or ""))
    if not sport:
        print(f"notify_open_match_by_sport: missing sport for event {event_id}")
        return

    created_by = str(payload.get("createdBy") or "")
    match_title = str(payload.get("title") or "Open match")
    modality = str(payload.get("modality") or "casual").strip().lower()
    max_participants = int(payload.get("maxParticipants") or 0)
    current_members = int(payload.get("membersCount") or 0)
    missing_players = max(max_participants - current_members, 0)

    db = firestore.client()
    creator_name = "Someone"
    creator_tokens: set[str] = set()
    if created_by:
        creator_doc = db.collection("users").document(created_by).get()
        if creator_doc.exists:
            creator_data = creator_doc.to_dict() or {}
            creator_name = resolve_user_display_name(creator_data)
            creator_tokens = collect_tokens_for_user_ids(db, {created_by})

    users_stream = db.collection("users").stream()

    target_user_ids: set[str] = set()
    for user_doc in users_stream:
        if not user_doc.exists:
            continue
        user_data = user_doc.to_dict() or {}
        user_id = user_doc.id
        if user_id == created_by:
            continue

        user_sports = parse_user_sports(user_data.get("mainSport"))
        if sport in user_sports:
            target_user_ids.add(user_id)

    if not target_user_ids:
        print(
            f"notify_open_match_by_sport: no users match sport={sport} for event={event_id}"
        )
        return

    tokens = collect_tokens_for_user_ids(db, target_user_ids)
    # Defense-in-depth: remove creator tokens even if user matching included creator
    # due to inconsistent user docs.
    if creator_tokens:
        tokens -= creator_tokens

    if not tokens:
        print(
            f"notify_open_match_by_sport: no tokens for matched users sport={sport} event={event_id}"
        )
        return

    sport_label = sport.title()
    title = f"{creator_name} wants to play {sport_label}"
    if missing_players > 0:
        body = (
            f"{creator_name} wants to play a {modality} game of {sport}. "
            f"Missing {missing_players} player(s). Tap to join in Play."
        )
    else:
        body = (
            f"{creator_name} opened a {modality} {sport} match: {match_title}. "
            "Tap to join in Play."
        )

    send_notification_to_tokens(
        tokens=tokens,
        title=title,
        body=body,
        data={
            "type": "open_match",
            "eventId": str(event_id),
            "sport": sport,
            "createdBy": created_by,
            "modality": modality,
            "missingPlayers": str(missing_players),
        },
    )

    print(
        f"notify_open_match_by_sport: done event={event_id}, sport={sport}, recipients={len(tokens)}"
    )
