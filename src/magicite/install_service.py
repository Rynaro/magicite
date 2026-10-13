"""Protected daemon entry point with narrowly verified stale-socket recovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from magicite.core.custody_admin import _store
from magicite.core.trust_custodian_transport import CustodianService, CustodyProfile
from magicite.install_privileged import remove_stale_socket


def main():
    directory, profile_path = map(Path, sys.argv[1:])
    profile = CustodyProfile.load(profile_path, expected_owner_uid=os.getuid(), maintenance=True)
    remove_stale_socket(profile.socket_path, profile.custodian_uid)
    with _store(directory) as store:
        head = store.read_current(profile.registry_id)
        if head["epoch"] != profile.epoch:
            raise RuntimeError("Custodian profile requires rotation maintenance")
        CustodianService(store, profile).serve()


if __name__ == "__main__":
    main()
