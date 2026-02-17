from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_FRIENDS_URL = "https://kapi.kakao.com/v1/api/talk/friends"
KAKAO_SEND_FRIEND_URL = "https://kapi.kakao.com/v1/api/talk/friends/message/default/send"
KAKAO_SEND_ME_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


class KakaoSendError(RuntimeError):
    """Raised when Kakao API call fails."""


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str


def parse_args() -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=".env", help="Path to .env file")
    pre_parser.add_argument("--no-dotenv", action="store_true", help="Disable .env loading")
    pre_args, _ = pre_parser.parse_known_args()
    if not pre_args.no_dotenv:
        load_dotenv(dotenv_path=pre_args.env_file)

    parser = argparse.ArgumentParser(description="Send text message to KakaoTalk friend (app member scope)")
    parser.add_argument("--env-file", default=pre_args.env_file, help="Path to .env file")
    parser.add_argument("--no-dotenv", action="store_true", help="Disable .env loading")
    parser.add_argument("--message-file", default="jobs_message.txt", help="Message text file path")
    parser.add_argument("--message-text", default="", help="Inline message text (overrides --message-file)")
    parser.add_argument(
        "--rest-api-key",
        default=os.getenv("KAKAO_REST_API_KEY", ""),
        help="Kakao REST API key (default: env KAKAO_REST_API_KEY)",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("KAKAO_CLIENT_SECRET", ""),
        help="Kakao client secret (default: env KAKAO_CLIENT_SECRET)",
    )
    parser.add_argument(
        "--token-file",
        default="kakao_tokens.json",
        help="JSON file containing refresh/access token",
    )
    parser.add_argument(
        "--refresh-token",
        default=os.getenv("KAKAO_REFRESH_TOKEN", ""),
        help="Refresh token (default: env KAKAO_REFRESH_TOKEN, then token file)",
    )
    parser.add_argument(
        "--access-token",
        default=os.getenv("KAKAO_ACCESS_TOKEN", ""),
        help="Access token fallback (default: env KAKAO_ACCESS_TOKEN, then token file)",
    )
    parser.add_argument("--receiver-uuid", default="", help="Target friend UUID")
    parser.add_argument("--receiver-name", default="", help="Target friend nickname")
    parser.add_argument("--list-friends", action="store_true", help="List available friends and exit")
    parser.add_argument(
        "--to-me",
        action="store_true",
        help="Send to myself instead of friend API (quick smoke test)",
    )
    parser.add_argument(
        "--web-url",
        default="https://www.jobkorea.co.kr/",
        help="Required link URL included in Kakao text template",
    )
    parser.add_argument("--button-title", default="채용 공고 보기", help="Button title in Kakao text template")
    parser.add_argument(
        "--max-text-length",
        type=int,
        default=200,
        help="Maximum text length per Kakao message chunk",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not call send API")
    parser.add_argument("--debug", action="store_true", help="Print debug logs to stderr")
    return parser.parse_args()


def _log(msg: str, debug: bool) -> None:
    if debug:
        print(msg, file=sys.stderr)


def _load_token_file(path: str) -> dict[str, Any]:
    if not path.strip():
        return {}
    token_path = Path(path)
    if not token_path.exists():
        return {}
    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KakaoSendError(f"Failed to read token file '{token_path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise KakaoSendError(f"Invalid JSON in token file '{token_path}': {exc}") from exc
    if not isinstance(payload, dict):
        raise KakaoSendError(f"Token file must contain JSON object: {token_path}")
    return payload


def _save_token_file(path: str, payload: dict[str, Any]) -> None:
    if not path.strip():
        return
    token_path = Path(path)
    token_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_tokens(args: argparse.Namespace, debug: bool) -> TokenBundle:
    token_file_data = _load_token_file(args.token_file)
    refresh_token = args.refresh_token.strip() or str(token_file_data.get("refresh_token", "")).strip()
    access_token = args.access_token.strip() or str(token_file_data.get("access_token", "")).strip()

    if not args.rest_api_key.strip():
        raise KakaoSendError("Missing REST API key. Pass --rest-api-key or set KAKAO_REST_API_KEY.")

    if refresh_token:
        refreshed = _refresh_access_token(
            rest_api_key=args.rest_api_key.strip(),
            refresh_token=refresh_token,
            client_secret=args.client_secret.strip(),
            debug=debug,
        )
        token_file_data["access_token"] = refreshed.access_token
        token_file_data["refresh_token"] = refreshed.refresh_token
        _save_token_file(args.token_file, token_file_data)
        return refreshed

    if not access_token:
        raise KakaoSendError(
            "No usable token. Provide refresh token (--refresh-token or token file), or access token (--access-token)."
        )
    return TokenBundle(access_token=access_token, refresh_token=refresh_token)


def _refresh_access_token(rest_api_key: str, refresh_token: str, client_secret: str, debug: bool) -> TokenBundle:
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    response = requests.post(KAKAO_AUTH_URL, data=data, timeout=15)
    if response.status_code >= 400:
        raise KakaoSendError(f"Failed to refresh access token: {response.status_code} {response.text}")

    payload = response.json()
    access_token = str(payload.get("access_token", "")).strip()
    if not access_token:
        raise KakaoSendError(f"Refresh response missing access_token: {payload}")

    updated_refresh = str(payload.get("refresh_token", "")).strip() or refresh_token
    _log("[token] access token refreshed", debug=debug)
    return TokenBundle(access_token=access_token, refresh_token=updated_refresh)


def _load_message(args: argparse.Namespace) -> str:
    inline = args.message_text
    if inline.strip():
        return inline.strip()

    path = Path(args.message_file)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KakaoSendError(f"Failed to read message file '{path}': {exc}") from exc

    text = text.strip()
    if not text:
        raise KakaoSendError(f"Message is empty: {path}")
    return text


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _fetch_friends(access_token: str) -> list[dict[str, Any]]:
    response = requests.get(
        KAKAO_FRIENDS_URL,
        headers=_auth_headers(access_token),
        params={"offset": 0, "limit": 100},
        timeout=15,
    )
    if response.status_code >= 400:
        raise KakaoSendError(f"Failed to fetch friends: {response.status_code} {response.text}")
    payload = response.json()
    elements = payload.get("elements", [])
    if not isinstance(elements, list):
        return []
    return [item for item in elements if isinstance(item, dict)]


def _pick_receiver_uuid_from_name(friends: list[dict[str, Any]], receiver_name: str) -> str:
    name = receiver_name.strip()
    if not name:
        raise KakaoSendError("Provide --receiver-name when receiver UUID is not set.")

    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    needle = name.casefold()
    for friend in friends:
        nickname = str(friend.get("profile_nickname", "")).strip()
        if not nickname:
            continue
        hay = nickname.casefold()
        if hay == needle:
            exact.append(friend)
        elif needle in hay:
            partial.append(friend)

    selected = exact if exact else partial
    if not selected:
        raise KakaoSendError(f"No friend matches nickname: '{name}'")
    if len(selected) > 1:
        candidates = ", ".join(str(item.get("profile_nickname", "")).strip() for item in selected)
        raise KakaoSendError(f"Nickname is ambiguous: '{name}'. candidates=[{candidates}]")

    picked_uuid = str(selected[0].get("uuid", "")).strip()
    if not picked_uuid:
        raise KakaoSendError(f"Matched friend has no uuid: '{name}'")
    return picked_uuid


def _chunk_message(text: str, max_len: int) -> list[str]:
    raw = text.strip()
    if not raw:
        return []
    if max_len <= 0:
        return [raw]

    chunks: list[str] = []
    remaining = raw
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len + 1)
        if cut <= 0:
            cut = max_len
        part = remaining[:cut].strip()
        if not part:
            part = remaining[:max_len]
            cut = max_len
        chunks.append(part)
        remaining = remaining[cut:].strip()
    return chunks


def _template_object(text: str, web_url: str, button_title: str) -> dict[str, Any]:
    return {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": web_url,
            "mobile_web_url": web_url,
        },
        "button_title": button_title,
    }


def _send_to_friend(access_token: str, receiver_uuid: str, text: str, web_url: str, button_title: str) -> dict[str, Any]:
    data = {
        "receiver_uuids": json.dumps([receiver_uuid], ensure_ascii=False),
        "template_object": json.dumps(_template_object(text=text, web_url=web_url, button_title=button_title), ensure_ascii=False),
    }
    response = requests.post(KAKAO_SEND_FRIEND_URL, headers=_auth_headers(access_token), data=data, timeout=15)
    if response.status_code >= 400:
        raise KakaoSendError(f"Friend send failed: {response.status_code} {response.text}")
    return response.json()


def _send_to_me(access_token: str, text: str, web_url: str, button_title: str) -> dict[str, Any]:
    data = {
        "template_object": json.dumps(_template_object(text=text, web_url=web_url, button_title=button_title), ensure_ascii=False),
    }
    response = requests.post(KAKAO_SEND_ME_URL, headers=_auth_headers(access_token), data=data, timeout=15)
    if response.status_code >= 400:
        raise KakaoSendError(f"Send-to-me failed: {response.status_code} {response.text}")
    return response.json()


def _print_friends(friends: list[dict[str, Any]]) -> None:
    if not friends:
        print("No friends returned from Kakao API.")
        return
    print(f"friends={len(friends)}")
    for idx, friend in enumerate(friends, start=1):
        nickname = str(friend.get("profile_nickname", "")).strip() or "-"
        uuid = str(friend.get("uuid", "")).strip() or "-"
        fav = "Y" if friend.get("favorite") else "N"
        print(f"{idx:02d}. nickname={nickname} favorite={fav} uuid={uuid}")


def main() -> None:
    args = parse_args()
    try:
        tokens = _resolve_tokens(args=args, debug=args.debug)

        if args.list_friends and not args.to_me:
            friends = _fetch_friends(tokens.access_token)
            _print_friends(friends)
            return

        message = _load_message(args)
        chunks = _chunk_message(message, max_len=args.max_text_length)
        if not chunks:
            raise KakaoSendError("Message is empty after chunking.")

        if args.to_me:
            for idx, chunk in enumerate(chunks, start=1):
                prefix = f"[{idx}/{len(chunks)}]\n" if len(chunks) > 1 else ""
                payload = _template_object(text=prefix + chunk, web_url=args.web_url, button_title=args.button_title)
                if args.dry_run:
                    print(f"[dry-run] to-me chunk={idx} text_len={len(prefix + chunk)} payload={json.dumps(payload, ensure_ascii=False)}")
                    continue
                response = _send_to_me(
                    access_token=tokens.access_token,
                    text=prefix + chunk,
                    web_url=args.web_url,
                    button_title=args.button_title,
                )
                _log(f"[send-to-me] chunk={idx}/{len(chunks)} response={response}", debug=args.debug)
            print(f"Sent {len(chunks)} message chunk(s) to me.")
            return

        receiver_uuid = args.receiver_uuid.strip()
        if not receiver_uuid:
            friends = _fetch_friends(tokens.access_token)
            receiver_uuid = _pick_receiver_uuid_from_name(
                friends=friends,
                receiver_name=args.receiver_name,
            )

        for idx, chunk in enumerate(chunks, start=1):
            text = f"[{idx}/{len(chunks)}]\n{chunk}" if len(chunks) > 1 else chunk
            payload = _template_object(text=text, web_url=args.web_url, button_title=args.button_title)
            if args.dry_run:
                print(
                    f"[dry-run] friend_uuid={receiver_uuid} chunk={idx} text_len={len(text)} "
                    f"payload={json.dumps(payload, ensure_ascii=False)}"
                )
                continue

            response = _send_to_friend(
                access_token=tokens.access_token,
                receiver_uuid=receiver_uuid,
                text=text,
                web_url=args.web_url,
                button_title=args.button_title,
            )
            _log(f"[send-friend] chunk={idx}/{len(chunks)} response={response}", debug=args.debug)

        print(f"Sent {len(chunks)} message chunk(s) to friend uuid={receiver_uuid}.")
    except KakaoSendError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
