"""About this file: a small client for the Kavita REST API (Kavita 0.9.1.4).

Covers what ebookprep needs: registering the first admin of a fresh Kavita (end-to-end
test), logging in, creating Book libraries with the settings Kavita's own UI uses,
scanning all libraries and waiting until the scan is committed, and listing every series
with the files it consists of. Endpoints and payloads were taken from Kavita's
openapi.json and checked against a running Kavita. A scan is complete when the library's
lastScanned changes; Kavita sets it after the scan's results are saved.

Kavita runs one scan at a time and postpones a scan requested while another runs by
hours. Creating a library starts its first scan, so the client waits for that scan before
returning, and rescans libraries one after another instead of using scan-all.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable

LIBRARY_TYPE_BOOK = 2
FILE_TYPES_EPUB_PDF = [2, 3]
# The value Kavita's UI uses for new libraries; 0 (unset) is rejected by the API.
METADATA_PROVIDER_DEFAULT = 3
PAGE_SIZE = 200


class KavitaError(Exception):
    """Raised when Kavita can't be reached or answers with an error."""


class KavitaClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 60,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None
        self.sleep, self.clock = sleep, clock

    # --- accounts

    def register_admin(self, username: str, password: str) -> None:
        """Creates the first user of a fresh Kavita, who becomes admin, and logs in."""
        self._request("POST", "/api/Account/register", {"username": username, "password": password, "email": ""})
        self.login(username, password)

    def login(self, username: str, password: str) -> None:
        user = self._request("POST", "/api/Account/login", {"username": username, "password": password})
        self.token = user["token"]

    # --- libraries

    def libraries(self) -> list[dict]:
        return self._request("GET", "/api/Library/libraries")

    def create_book_library(self, name: str, folders: list[str], timeout: float = 1800, poll: float = 2) -> dict:
        """Creates a Book library like Kavita's UI does and waits for its first scan."""
        library = self._request(
            "POST",
            "/api/Library/create",
            {
                "id": 0, "name": name, "type": LIBRARY_TYPE_BOOK, "folders": folders, "folderWatching": True,
                "includeInDashboard": True, "includeInSearch": True, "manageCollections": False,
                "manageReadingLists": False, "allowScrobbling": False, "allowMetadataMatching": False,
                "enableMetadata": True, "removePrefixForSortName": False, "inheritWebLinksFromFirstChapter": False,
                "fileGroupTypes": FILE_TYPES_EPUB_PDF, "excludePatterns": [],
                "metadataProvider": METADATA_PROVIDER_DEFAULT,
            },
        )
        self._wait_until_scanned(library["id"], library["lastScanned"], name, timeout, poll)
        return library

    def scan_all_and_wait(self, timeout: float = 1800, poll: float = 2) -> None:
        """Rescans every library, one after another, each within timeout seconds."""
        for library in self.libraries():
            self._request("POST", f"/api/Library/scan?libraryId={library['id']}&force=true")
            self._wait_until_scanned(library["id"], library["lastScanned"], library["name"], timeout, poll)

    def _wait_until_scanned(self, library_id: int, before: str, name: str, timeout: float, poll: float) -> None:
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            self.sleep(poll)
            current = next((l for l in self.libraries() if l["id"] == library_id), None)
            if current is not None and current["lastScanned"] != before:
                return
        raise KavitaError(f"Kavita didn't finish scanning library {name!r} within {timeout} seconds")

    # --- series

    def all_series(self) -> list[dict]:
        series: list[dict] = []
        page = 1
        while True:
            batch = self._request(
                "POST",
                f"/api/Series/all-v2?PageNumber={page}&PageSize={PAGE_SIZE}",
                {"statements": [], "combination": 1, "limitTo": 0},
            )
            series += batch
            if len(batch) < PAGE_SIZE:
                return series
            page += 1

    def series_files(self, series_id: int) -> list[str]:
        volumes = self._request("GET", f"/api/Series/volumes?seriesId={series_id}")
        return [file["filePath"] for volume in volumes for chapter in volume["chapters"] for file in chapter["files"]]

    # --- HTTP

    def _request(self, method: str, path: str, body: dict | None = None):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read()[:300].decode("utf-8", errors="replace")
            raise KavitaError(f"{method} {path} failed with HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, OSError) as error:
            raise KavitaError(f"Kavita at {self.base_url} not reachable: {error}") from error
        return json.loads(raw) if raw.strip()[:1] in (b"{", b"[") else None
