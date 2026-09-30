"""Typed dual-key rotation certificates; never accepts private keys from clients."""

from __future__ import annotations

from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from magicite.core.trust_custodian import CustodianError, _bytes

TRANSITION_DOMAIN = b"magicite-trust-transition/1\x00"


def verify_transition(certificate: dict[str, Any]) -> dict[str, Any]:
    """Verify both directions of the exact immutable epoch/head commitment.

    Caller MUST separately pin the old or new public key from protected custody;
    a self-consistent certificate is not enrollment authority.
    """
    try:
        if set(certificate) != {"body", "old_signature", "new_signature"}:
            raise ValueError("fields")
        body = certificate["body"]
        required = {
            "schema",
            "registry_id",
            "transition_id",
            "old_epoch",
            "new_epoch",
            "old_public_key",
            "new_public_key",
            "old_key_id",
            "new_key_id",
            "old_head",
            "new_head",
            "policy_digest",
            "intent_digest",
        }
        if (
            set(body) != required
            or body["schema"] != "Transition/1"
            or type(body["old_epoch"]) is not int
            or type(body["new_epoch"]) is not int
            or body["new_epoch"] != body["old_epoch"] + 1
            or body["old_epoch"] < 1
            or body["old_head"]["epoch"] != body["old_epoch"]
            or body["new_head"]["epoch"] != body["new_epoch"]
            or body["new_head"]["head_sequence"] != body["old_head"]["head_sequence"] + 1
            or any(body[key]["registry_id"] != body["registry_id"] for key in ("old_head", "new_head"))
        ):
            raise ValueError("binding")
        import hashlib

        for prefix in ("old", "new"):
            raw = bytes.fromhex(body[f"{prefix}_public_key"])
            if hashlib.sha256(raw).hexdigest() != body[f"{prefix}_key_id"]:
                raise ValueError("key identity")
            Ed25519PublicKey.from_public_bytes(raw).verify(
                bytes.fromhex(certificate[f"{prefix}_signature"]), TRANSITION_DOMAIN + _bytes(body)
            )
        return body
    except (ValueError, TypeError, KeyError, RecursionError, InvalidSignature) as exc:
        raise CustodianError("invalid custody epoch transition") from exc


def verify_transition_record(certificate: dict[str, Any], record: dict[str, Any]) -> None:
    """Bind the signed non-circular certificate to the exact prepared record."""
    import hashlib

    body = verify_transition(certificate)
    try:
        intent = record["payload"]
        if (
            record["kind"] != "epoch_transition"
            or record["record_id"] != "epoch-" + body["transition_id"]
            or record["registry_id"] != body["registry_id"]
            or record["epoch"] != body["new_epoch"]
            or record["sequence"] != body["new_head"]["head_sequence"]
            or record["mac"] != body["new_head"]["head_mac"]
            or record["prev_mac"] != body["old_head"]["head_mac"]
            or hashlib.sha256(_bytes(intent)).hexdigest() != body["intent_digest"]
            or record["payload_digest"] != body["intent_digest"]
            or intent["schema"] != "EpochTransitionIntent/1"
            or any(
                intent[key] != body[key]
                for key in (
                    "registry_id",
                    "transition_id",
                    "old_epoch",
                    "new_epoch",
                    "old_public_key",
                    "new_public_key",
                    "old_head",
                    "policy_digest",
                )
            )
        ):
            raise ValueError("transition record binding")
    except (KeyError, ValueError, TypeError) as exc:
        raise CustodianError("invalid custody transition record") from exc
