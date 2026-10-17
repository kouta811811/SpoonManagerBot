"""起動・停止のControl Lambda（要件R3、設計§4）。

公開URLは持たず、IAM認証済みの `aws lambda invoke` からだけ呼ぶ。

    {"action": "start"}   leaseを取ってFargate Taskを1つ起動する
    {"action": "stop"}    起動中のTaskを止める
    {"action": "status"}  leaseの状態を返す
    {"action": "reset"}   止まったTaskのleaseが残ったときに消す
                          （Taskが動いていないことを確認してから）
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

from spoon_manager_bot import aws
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.repositories.state import LeaseRepository

START_LEASE = 300.0  # Taskの起動とイメージ取得にかかる時間を見込む


class Controller:
    def __init__(
        self,
        *,
        ecs: Any,
        lease: LeaseRepository,
        cluster: str,
        task_definition: str,
        container: str,
        subnets: list[str],
        security_group: str,
    ) -> None:
        self._ecs = ecs
        self._lease = lease
        self._cluster = cluster
        self._task_definition = task_definition
        self._container = container
        self._subnets = subnets
        self._security_group = security_group

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        action = str(event.get("action", "status"))
        if action == "start":
            return self.start()
        if action == "stop":
            return self.stop()
        if action == "reset":
            return self.reset()
        return self.status()

    def start(self) -> dict[str, Any]:
        token = secrets.token_urlsafe(16)
        if not self._lease.acquire(token, START_LEASE):
            current = self._lease.get()
            return {"result": "already_running", "task_arn": current.task_arn if current else None}
        try:
            response = self._ecs.run_task(
                cluster=self._cluster,
                taskDefinition=self._task_definition,
                launchType="FARGATE",
                count=1,
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self._subnets,
                        "securityGroups": [self._security_group],
                        "assignPublicIp": "ENABLED",
                    }
                },
                overrides={
                    "containerOverrides": [
                        {
                            "name": self._container,
                            "environment": [{"name": "LEASE_TOKEN", "value": token}],
                        }
                    ]
                },
                startedBy="control-lambda",
            )
        except Exception:
            self._lease.release(token)
            log_event("control_start_failed", result="run_task_error", level=40)
            raise
        tasks = response.get("tasks") or []
        if not tasks:
            self._lease.release(token)
            failures = response.get("failures") or []
            reason = failures[0].get("reason") if failures else "unknown"
            log_event("control_start_failed", result="no_task", error_code=str(reason), level=40)
            return {"result": "failed", "reason": reason}
        task_arn = tasks[0]["taskArn"]
        self._lease.set_task(token, task_arn)
        log_event("control_started", result="ok")
        return {"result": "started", "task_arn": task_arn}

    def stop(self) -> dict[str, Any]:
        current = self._lease.get()
        if current is None or current.task_arn is None:
            return {"result": "not_running"}
        self._ecs.stop_task(
            cluster=self._cluster, task=current.task_arn, reason="Stopped by operator"
        )
        log_event("control_stopped", result="ok")
        # leaseはWorkerが終了時に外す。外せなかった場合も期限（最大2分）で切れる。
        return {"result": "stopping", "task_arn": current.task_arn}

    def status(self) -> dict[str, Any]:
        current = self._lease.get()
        if current is None:
            return {"result": "not_running"}
        remaining = int(current.expires_at - time.time())
        return {
            "result": "running" if remaining > 0 else "stale",
            "task_arn": current.task_arn,
            "lease_seconds_left": max(0, remaining),
        }

    def reset(self) -> dict[str, Any]:
        current = self._lease.get()
        if current is not None and current.task_arn:
            described = self._ecs.describe_tasks(cluster=self._cluster, tasks=[current.task_arn])
            for task in described.get("tasks") or []:
                if task.get("lastStatus") not in ("STOPPED", "DEPROVISIONING"):
                    return {"result": "task_still_running", "task_arn": current.task_arn}
        self._lease.force_release()
        return {"result": "reset"}


def _controller() -> Controller:
    region = os.environ.get("AWS_REGION")
    return Controller(
        ecs=aws.client("ecs", region),
        lease=LeaseRepository(aws.client("dynamodb", region), os.environ["STATE_TABLE"]),
        cluster=os.environ["ECS_CLUSTER"],
        task_definition=os.environ["TASK_DEFINITION"],
        container=os.environ["CONTAINER_NAME"],
        subnets=[s for s in os.environ["SUBNET_IDS"].split(",") if s],
        security_group=os.environ["SECURITY_GROUP_ID"],
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return _controller().handle(event if isinstance(event, dict) else {})
