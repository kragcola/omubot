"""Run the smallest isolated chat loop against the real application boundary."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from omubot_new.adapters import OfflineModel, OfflineSender
from omubot_new.bootstrap import create_app
from omubot_new.config import Config, Credentials, ModelProfile
from omubot_new.types import Grant, Scope

BOT_ID = "10001"
GROUP_ID = "20001"
SUBJECT = "30001"


def _credentials() -> Credentials:
    values = [secrets.token_urlsafe(32) for _ in range(3)]
    return Credentials(admin=values[0], status=values[1], ingress=values[2])


def _event(message_id: int, text: str, *, group_id: str = GROUP_ID) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": "group",
        "self_id": BOT_ID,
        "group_id": group_id,
        "user_id": SUBJECT,
        "message_id": message_id,
        "message": text,
    }


def _grant(config: Config) -> Grant:
    return Grant(
        subject=SUBJECT,
        provider=config.policy_provider,
        model=config.model,
        scope=Scope(bot_id=BOT_ID, group_id=GROUP_ID),
        actions=["message.read", "model.invoke", "message.reply", "tool.invoke:time.now"],
        expires_at=time.time() + 300,
    )


def _post_event(client: TestClient, credentials: Credentials, payload: dict[str, Any]) -> Any:
    return client.post(
        "/offline/events",
        headers={"Authorization": "Bearer " + credentials.admin},
        json=payload,
    )


def run_demo() -> dict[str, object]:
    """Exercise the offline chat and time paths with disposable synthetic state."""
    credentials = _credentials()
    temp_dir = ""
    with TemporaryDirectory(prefix="omubot-demo-") as directory:
        temp_dir = directory
        config = Config(
            db_path=str(Path(directory) / "demo.sqlite3"),
            mode="offline",
            models={"default": ModelProfile(endpoint="http://127.0.0.1:1")},
            onebot_endpoint="http://127.0.0.1:1",
        )
        sender = OfflineSender()
        app = create_app(config, credentials, model=OfflineModel(), sender=sender)

        with TestClient(app) as client:
            status_without_credentials = client.get("/api/status")
            assert status_without_credentials.status_code == 401

            policy_response = client.put(
                "/internal/policy",
                headers={"Authorization": "Bearer " + credentials.admin},
                json={"expected_revision": 0, "grants": [_grant(config).model_dump(mode="json")]},
            )
            assert policy_response.status_code == 200

            normal = _post_event(client, credentials, _event(1, "hello"))
            query_time = _post_event(client, credentials, _event(2, "查询时间"))
            assert normal.status_code == 200 and query_time.status_code == 200
            assert normal.json()["state"] == "succeeded"
            assert query_time.json()["state"] == "succeeded"

            duplicate = _post_event(client, credentials, _event(1, "hello"))
            assert duplicate.status_code == 409

            different_group = _post_event(client, credentials, _event(3, "hello", group_id="20002"))
            assert different_group.status_code == 403

            status = client.get("/api/status", headers={"Authorization": "Bearer " + credentials.status})
            assert status.status_code == 200
            status_data = status.json()
            action_count = status_data["actions"]
            assert action_count.get("succeeded") == 7

            # OfflineSender.close() clears its sample buffer, so copy replies before lifespan exit.
            replies = [text for _, text in sender.sent]
            assert len(replies) == 2

            result: dict[str, object] = {
                "mode": status_data["mode"],
                "scenarios": {
                    "normal_text": {"state": normal.json()["state"], "reply": replies[0]},
                    "query_time": {"state": query_time.json()["state"], "reply": replies[1]},
                },
                "replies": replies,
                "action_count": {"succeeded": action_count["succeeded"]},
                "checks": {
                    "policy_grant": True,
                    "duplicate_rejected": True,
                    "different_group_rejected": True,
                    "status_auth_rejected_without_credentials": True,
                },
            }

    sandbox_removed = not Path(temp_dir).exists()
    assert sandbox_removed
    checks = result["checks"]
    assert isinstance(checks, dict)
    checks["temporary_data_cleaned"] = sandbox_removed
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行 Omubot 最小离线闭环演示")
    parser.add_argument("--output", type=Path, help="独占创建 JSON 输出文件")
    args = parser.parse_args()
    rendered = json.dumps(run_demo(), ensure_ascii=False, separators=(",", ":"))
    if args.output is None:
        print(rendered)
        return
    try:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    except FileExistsError:
        parser.error("输出文件已存在，拒绝覆盖")


if __name__ == "__main__":
    main()
