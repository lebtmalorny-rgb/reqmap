"""Verify immutable binding manifest bytes under the existing deep signer policy."""
from pathlib import Path
import subprocess
import tempfile

from reqmap.agent_input import read_regular_bytes
from reqmap.errors import ReqmapError
from reqmap.snapshot_trust import _require_ed25519_signature, _require_ed25519_signer


def verify_binding_signature(root: Path, allowed_signers_path: Path | None,
                             manifest: bytes, signer_identity: str) -> None:
    if (allowed_signers_path is None or signer_identity != "reqmap-snapshot"
            or allowed_signers_path.absolute().is_relative_to(root.absolute())):
        raise ReqmapError("BINDING_UNTRUSTED", "BINDING_UNTRUSTED: требуется внешний trust deep snapshot.")
    try:
        signers = read_regular_bytes(allowed_signers_path, 1024 * 1024)
        signature = read_regular_bytes(root / "binding-manifest.sig", 1024 * 1024)
        _require_ed25519_signature(signature)
        # Freeze both inputs: verifier must not reopen mutable catalog/trust paths.
        with tempfile.TemporaryDirectory(prefix="reqmap-binding-verify-") as temporary:
            directory = Path(temporary)
            signer_file, signature_file = directory / "allowed_signers", directory / "signature"
            signer_file.write_bytes(signers)
            signature_file.write_bytes(signature)
            _require_ed25519_signer(signer_file)
            result = subprocess.run(
                ["ssh-keygen", "-Y", "verify", "-f", str(signer_file),
                 "-I", signer_identity, "-n", "reqmap-obligation-binding", "-s", str(signature_file)],
                input=manifest, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False, shell=False,
            )
        if result.returncode != 0:
            raise ValueError("signature rejected")
    except (OSError, ValueError, subprocess.TimeoutExpired, ReqmapError) as exc:
        raise ReqmapError("BINDING_UNTRUSTED", "BINDING_UNTRUSTED: подпись каталога не подтверждена.") from exc
