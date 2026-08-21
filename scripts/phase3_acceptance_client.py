"""Drive a real resumable upload through a running StudyAI HTTP server."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
META_CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')


class Client:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.csrf = ""

    def get_html(self, path: str) -> str:
        with self.opener.open(self.base_url + path) as response:
            return response.read().decode("utf-8")

    def form(self, path: str, values: dict[str, str]):
        body = urllib.parse.urlencode(values).encode()
        request = urllib.request.Request(self.base_url + path, data=body, method="POST")
        return self.opener.open(request)

    def api(self, path: str, method: str = "GET", body=None, content_type=None):
        headers = {"X-CSRF-Token": self.csrf}
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


def authenticate(client: Client, args) -> None:
    login_html = client.get_html("/login")
    token = _csrf(login_html)
    response = client.form(
        "/login", {"csrf_token": token, "username": args.username, "password": args.password}
    )
    if response.geturl().endswith("/dashboard"):
        dashboard = response.read().decode()
    else:
        register_html = client.get_html("/register")
        token = _csrf(register_html)
        client.form(
            "/register",
            {
                "csrf_token": token,
                "name": "Phase 3 Acceptance",
                "username": args.username,
                "email": args.email,
                "password": args.password,
            },
        ).read()
        token = _csrf(client.get_html("/login"))
        response = client.form(
            "/login",
            {"csrf_token": token, "username": args.username, "password": args.password},
        )
        if not response.geturl().endswith("/dashboard"):
            raise RuntimeError("Could not authenticate acceptance user")
        dashboard = response.read().decode()
    match = META_CSRF_RE.search(dashboard)
    if not match:
        raise RuntimeError("Dashboard CSRF token is missing")
    client.csrf = match.group(1)


def submit(client: Client, media: Path) -> None:
    status, upload = client.api(
        "/api/uploads",
        "POST",
        {"filename": media.name, "total_size": media.stat().st_size},
    )
    assert status == 201, upload
    upload_id = upload["upload_id"]
    chunk_size = upload["chunk_size"]
    chunks = []
    with media.open("rb") as source:
        while chunk := source.read(chunk_size):
            chunks.append(chunk)
    assert len(chunks) >= 3

    for index in range(2):
        status, _ = client.api(
            f"/api/uploads/{upload_id}/chunks/{index}",
            "PUT",
            chunks[index],
            "application/octet-stream",
        )
        assert status == 200
    status, _ = client.api(
        f"/api/uploads/{upload_id}/chunks/0",
        "PUT",
        chunks[0],
        "application/octet-stream",
    )
    assert status == 200
    status, progress = client.api(f"/api/uploads/{upload_id}")
    assert status == 200 and progress["received_chunks"] == 2
    status, _ = client.api(f"/api/uploads/{upload_id}/complete", "POST")
    assert status == 409

    for index in range(2, len(chunks)):
        status, _ = client.api(
            f"/api/uploads/{upload_id}/chunks/{index}",
            "PUT",
            chunks[index],
            "application/octet-stream",
        )
        assert status == 200
    status, queued = client.api(f"/api/uploads/{upload_id}/complete", "POST")
    assert status == 202, queued
    print(
        json.dumps(
            {
                "upload_id": upload_id,
                "job_id": queued["job_id"],
                "chunks": len(chunks),
                "size": media.stat().st_size,
            }
        )
    )


def _csrf(html: str) -> str:
    match = CSRF_RE.search(html)
    if not match:
        raise RuntimeError("CSRF token is missing")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    client = Client(args.base_url)
    authenticate(client, args)
    submit(client, args.media)


if __name__ == "__main__":
    main()
