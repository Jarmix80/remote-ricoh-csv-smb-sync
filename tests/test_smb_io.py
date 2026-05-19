from __future__ import annotations

from pathlib import PureWindowsPath

import remote_ricoh.smb_io as smb_io
from remote_ricoh.smb_io import SmbClient


def test_smb_client_normalizes_unc() -> None:
    client = SmbClient(remote_unc="//srv/share/base", username="u", password="p")
    assert client.server == "srv"
    assert client.base_unc == r"\\srv\share\base"


def test_write_binary_adds_increment_suffix_when_name_exists(monkeypatch) -> None:
    client = SmbClient(remote_unc="//srv/share/base", username="u", password="p")
    target = str(PureWindowsPath(client.base_unc) / "DPLAC_12-05-2026.csv")
    existing = {
        target,
        str(PureWindowsPath(client.base_unc) / "DPLAC_12-05-2026(1).csv"),
    }

    written: list[tuple[str, str, bytes]] = []

    class DummyFile:
        def __init__(self, path: str, mode: str) -> None:
            self.path = path
            self.mode = mode

        def __enter__(self) -> DummyFile:
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def write(self, payload: bytes) -> None:
            written.append((self.path, self.mode, payload))

    monkeypatch.setattr(smb_io.smbclient.path, "exists", lambda path: path in existing)
    monkeypatch.setattr(smb_io.smbclient, "makedirs", lambda *args, **kwargs: None)
    monkeypatch.setattr(smb_io.smbclient, "open_file", lambda path, mode: DummyFile(path, mode))

    out = client.write_binary(["DPLAC_12-05-2026.csv"], b"payload")

    assert out == str(PureWindowsPath(client.base_unc) / "DPLAC_12-05-2026(2).csv")
    assert written == [(out, "wb", b"payload")]
