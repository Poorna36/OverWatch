"""Gateway-protected Calendar & Gmail MCP tool execution and user send (F-18)."""

from __future__ import annotations

from typing import Any

import httpx
from control_plane.gateway.argument_validation import ValidatedArguments
from control_plane.vault.google_vault import GoogleConnectorVault, VaultAuthError

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class ConnectorExecutionError(Exception):
    """Raised when a Google Calendar or Gmail API request fails."""


class GoogleConnectorClient:
    """Executes Calendar/Gmail tool calls using encrypted vault credentials."""

    def __init__(
        self,
        *,
        vault: GoogleConnectorVault,
        http_client: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._vault = vault
        self._http_client = http_client
        self._timeout_s = timeout_s

    def _request(
        self,
        *,
        owner_id: str,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tokens = self._vault.get_tokens(owner_id=owner_id)
        headers = {"Authorization": f"Bearer {tokens.access_token}"}
        if self._http_client is not None:
            resp = self._http_client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self._timeout_s,
            )
        else:
            with httpx.Client(timeout=self._timeout_s) as client:
                resp = client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                )

        if resp.status_code in (401, 403):
            raise VaultAuthError(
                f"Google API rejected credential (HTTP {resp.status_code})."
            )
        if resp.status_code >= 400:
            raise ConnectorExecutionError(
                f"Google API error (HTTP {resp.status_code})."
            )
        if not resp.content:
            return {"ok": True}
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}

    def execute_tool(
        self, *, owner_id: str, validated: ValidatedArguments
    ) -> dict[str, Any]:
        """Execute a gateway-validated Calendar or Gmail tool call."""
        tool = validated.tool
        args = validated.args

        if tool == "calendar.list_events":
            params: dict[str, Any] = {"maxResults": args.get("max_results", 10)}
            if "time_min" in args:
                params["timeMin"] = args["time_min"]
            if "time_max" in args:
                params["timeMax"] = args["time_max"]
            return self._request(
                owner_id=owner_id,
                method="GET",
                url=f"{CALENDAR_API_BASE}/calendars/primary/events",
                params=params,
            )

        if tool == "calendar.create_event":
            body: dict[str, Any] = {
                "summary": args["summary"],
                "start": {"dateTime": args["start"]},
                "end": {"dateTime": args["end"]},
            }
            if "description" in args:
                body["description"] = args["description"]
            return self._request(
                owner_id=owner_id,
                method="POST",
                url=f"{CALENDAR_API_BASE}/calendars/primary/events",
                json_body=body,
            )

        if tool == "calendar.update_event":
            event_id = args["event_id"]
            body = {}
            for field in ("summary", "description"):
                if field in args:
                    body[field] = args[field]
            if "start" in args:
                body["start"] = {"dateTime": args["start"]}
            if "end" in args:
                body["end"] = {"dateTime": args["end"]}
            return self._request(
                owner_id=owner_id,
                method="PATCH",
                url=f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
                json_body=body,
            )

        if tool == "calendar.delete_event":
            event_id = args["event_id"]
            self._request(
                owner_id=owner_id,
                method="DELETE",
                url=f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            )
            return {"deleted": True, "event_id": event_id}

        if tool == "gmail.search":
            return self._request(
                owner_id=owner_id,
                method="GET",
                url=f"{GMAIL_API_BASE}/users/me/messages",
                params={"q": args["query"], "maxResults": args.get("max_results", 10)},
            )

        if tool == "gmail.read_message":
            msg_id = args["message_id"]
            return self._request(
                owner_id=owner_id,
                method="GET",
                url=f"{GMAIL_API_BASE}/users/me/messages/{msg_id}",
            )

        if tool == "gmail.create_draft":
            raw_rfc822 = (
                f"To: {args['to']}\r\n"
                f"Subject: {args['subject']}\r\n\r\n"
                f"{args['body']}"
            )
            import base64

            raw_b64 = base64.urlsafe_b64encode(raw_rfc822.encode("utf-8")).decode(
                "ascii"
            )
            return self._request(
                owner_id=owner_id,
                method="POST",
                url=f"{GMAIL_API_BASE}/users/me/drafts",
                json_body={"message": {"raw": raw_b64}},
            )

        raise ConnectorExecutionError(f"Unsupported connector tool {tool!r}.")

    def send_user_draft(self, *, owner_id: str, draft_id: str) -> dict[str, Any]:
        """Send an existing Gmail draft ONLY via user-originated API action."""
        return self._request(
            owner_id=owner_id,
            method="POST",
            url=f"{GMAIL_API_BASE}/users/me/drafts/send",
            json_body={"id": draft_id},
        )
