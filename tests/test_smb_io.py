from __future__ import annotations

from remote_ricoh.smb_io import SmbClient


def test_smb_client_normalizes_unc() -> None:
    client = SmbClient(remote_unc="//srv/share/base", username="u", password="p")
    assert client.server == "srv"
    assert client.base_unc == r"\\srv\share\base"
