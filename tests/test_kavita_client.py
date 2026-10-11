"""About this file: tests for ebookprep.kavita, the Kavita API client, against a simulated
Kavita that behaves like the real one: creating a library starts its first scan, and only
one scan runs at a time (Kavita postpones scans requested meanwhile by hours)."""

import pytest

from ebookprep.kavita import KavitaClient, KavitaError


class SimulatedKavita(KavitaClient):
    def __init__(self, scan_seconds: float = 4):
        self.now = 0.0
        super().__init__("http://kavita", sleep=self._sleep, clock=lambda: self.now)
        self.libs: list[dict] = []
        self.running: tuple[int, float] | None = None  # (library id, finishes at)
        self.postponed: list[int] = []
        self.scan_seconds = scan_seconds
        self.requests: list[str] = []

    def _sleep(self, seconds):
        self.now += seconds
        if self.running and self.now >= self.running[1]:
            library_id, _ = self.running
            next(l for l in self.libs if l["id"] == library_id)["lastScanned"] = f"t{self.now}"
            self.running = None

    def _start_scan(self, library_id):
        if self.running:
            self.postponed.append(library_id)
        else:
            self.running = (library_id, self.now + self.scan_seconds)

    def _request(self, method, path, body=None):
        self.requests.append(f"{method} {path}")
        if path == "/api/Library/libraries":
            return [dict(l) for l in self.libs]
        if path == "/api/Library/create":
            library = {"id": len(self.libs) + 1, "name": body["name"], "lastScanned": "0001-01-01T00:00:00"}
            self.libs.append(library)
            self._start_scan(library["id"])
            return dict(library)
        if path.startswith("/api/Library/scan?"):
            self._start_scan(int(path.split("libraryId=")[1].split("&")[0]))
            return None
        raise AssertionError(f"unexpected request {method} {path}")


def test_creating_libraries_waits_for_each_first_scan():
    kavita = SimulatedKavita()

    for name in ("IT", "Raw", "Spiritualität"):
        kavita.create_book_library(name, [f"/library/{name}"])

    assert kavita.postponed == []
    assert all(l["lastScanned"] != "0001-01-01T00:00:00" for l in kavita.libs)


def test_scan_all_scans_libraries_one_after_another():
    kavita = SimulatedKavita()
    for name in ("IT", "Raw"):
        kavita.create_book_library(name, [f"/library/{name}"])
    before = [l["lastScanned"] for l in kavita.libs]

    kavita.scan_all_and_wait()

    assert kavita.postponed == []
    assert [l["lastScanned"] for l in kavita.libs] != before
    assert all(a != b for a, b in zip(before, (l["lastScanned"] for l in kavita.libs)))
    assert [r for r in kavita.requests if "scan?" in r] == [
        "POST /api/Library/scan?libraryId=1&force=true",
        "POST /api/Library/scan?libraryId=2&force=true",
    ]


def test_scan_that_never_finishes_times_out():
    kavita = SimulatedKavita(scan_seconds=10_000)

    with pytest.raises(KavitaError, match="IT"):
        kavita.create_book_library("IT", ["/library/IT"], timeout=60)
