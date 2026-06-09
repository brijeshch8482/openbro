"""One-time-setup helper for `openbro train`.

Walks through the pre-flight checks: ML libraries installed, CUDA
available, HuggingFace logged in, gh CLI authenticated, llama.cpp
located. Prints a checklist of green / red items so the maintainer
knows exactly what's blocking the first training run.

Where automation is safe (e.g. cloning llama.cpp), the helper offers
to do it. Anything that requires the user's account credentials
(`hf login`, `gh auth login`, Meta license acceptance) is gated
behind a clear instruction the user runs themselves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix_hint: str = ""


def _which(cmd: str) -> str | None:
    """Return absolute path of `cmd` if on PATH, else None."""
    return shutil.which(cmd)


def check_python_packages() -> CheckResult:
    """All four core ML packages importable."""
    missing: list[str] = []
    for mod in ("torch", "transformers", "peft", "bitsandbytes"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return CheckResult(
            "Python ML packages",
            False,
            f"missing: {', '.join(missing)}",
            "Install training extras:\n"
            "  pip install 'openbro[training]' "
            "--extra-index-url https://download.pytorch.org/whl/cu121",
        )
    return CheckResult(
        "Python ML packages", True, "torch, transformers, peft, bitsandbytes installed"
    )


def _nvidia_driver_version() -> str | None:
    """Return the human-readable driver version from `nvidia-smi`,
    or None if the binary isn't on PATH (no NVIDIA hardware or
    driver not installed)."""
    if _which("nvidia-smi") is None:
        return None
    try:
        p = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return p.stdout.strip().splitlines()[0] if p.stdout.strip() else None


def check_cuda() -> CheckResult:
    """CUDA visible to PyTorch. Detects the common 'driver too old'
    case explicitly so the fix hint points at the driver update
    rather than re-installing torch (which won't help)."""
    try:
        import torch
    except ImportError:
        return CheckResult(
            "CUDA",
            False,
            "torch not installed",
            "Install training extras first (see above).",
        )
    if not torch.cuda.is_available():
        driver = _nvidia_driver_version()
        torch_cuda = getattr(torch.version, "cuda", "?")
        # PyTorch 2.x wheels for cu121 need NVIDIA driver >= 525 (Linux)
        # or >= 528 (Windows). cu118 wheels need >= 450/452.
        msg = f"torch.cuda.is_available() is False (torch built for CUDA {torch_cuda}"
        if driver:
            msg += f", driver {driver}"
        msg += ")"
        if driver and torch_cuda == "12.1":
            hint = (
                f"Your NVIDIA driver ({driver}) is too old for torch+cu121.\n"
                f"Update the driver to >= 528 (Windows) or >= 525 (Linux):\n"
                f"  https://www.nvidia.com/Download/index.aspx\n"
                f"OR install a torch wheel that matches your existing driver:\n"
                f"  pip install --upgrade --force-reinstall torch \\\n"
                f"    --index-url https://download.pytorch.org/whl/cu118"
            )
        else:
            hint = (
                "Verify driver with `nvidia-smi`. If old, update at "
                "https://www.nvidia.com/Download/index.aspx — otherwise "
                "match the torch wheel to your driver's CUDA version."
            )
        return CheckResult("CUDA", False, msg, hint)
    name = torch.cuda.get_device_name(0)
    vram_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
    return CheckResult("CUDA", True, f"{name} ({vram_gb} GB VRAM)")


def check_huggingface_login() -> CheckResult:
    """User has a valid HuggingFace token saved."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return CheckResult(
            "HuggingFace login",
            False,
            "huggingface_hub not installed",
            "Install training extras first.",
        )
    try:
        info = HfApi().whoami()
    except Exception as e:
        return CheckResult(
            "HuggingFace login",
            False,
            f"not logged in ({type(e).__name__})",
            "Login once:\n  huggingface-cli login\n"
            "Get a token at https://huggingface.co/settings/tokens",
        )
    return CheckResult("HuggingFace login", True, f"logged in as {info.get('name', '?')}")


def check_gh_cli() -> CheckResult:
    """gh CLI installed and authenticated."""
    if _which("gh") is None:
        return CheckResult(
            "gh CLI",
            False,
            "not on PATH",
            "Install GitHub CLI: https://cli.github.com — then `gh auth login`.",
        )
    p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if p.returncode != 0:
        return CheckResult(
            "gh CLI",
            False,
            "not authenticated",
            "Run: gh auth login",
        )
    return CheckResult("gh CLI", True, "authenticated")


def _find_quantize_binary(llama_dir: Path) -> Path | None:
    """Locate the llama-quantize binary inside a llama.cpp checkout.

    The binary moves around between platforms and build configs:
      • Linux/Mac Makefile:  <root>/llama-quantize
      • Windows CMake:       <root>/build/bin/Release/llama-quantize.exe
      • Legacy:              <root>/quantize(.exe)
    Returns the first one that exists or None.
    """
    candidates = [
        llama_dir / "llama-quantize",
        llama_dir / "llama-quantize.exe",
        llama_dir / "build" / "bin" / "Release" / "llama-quantize.exe",
        llama_dir / "build" / "bin" / "llama-quantize",
        llama_dir / "build" / "llama-quantize",
        llama_dir / "quantize",
        llama_dir / "quantize.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def check_llama_cpp() -> CheckResult:
    """llama.cpp checkout + convert script + built quantize binary."""
    llama_dir = os.environ.get("OPENBRO_LLAMA_CPP_DIR", "./llama.cpp")
    p = Path(llama_dir)
    if not p.exists():
        return CheckResult(
            "llama.cpp",
            False,
            f"not found at {p}",
            "Clone + build:\n"
            "  git clone https://github.com/ggerganov/llama.cpp\n"
            "  cd llama.cpp && cmake -B build && cmake --build build --config Release\n"
            "Then set:\n"
            '  setx OPENBRO_LLAMA_CPP_DIR "D:\\\\llama.cpp"',
        )
    has_convert = (p / "convert_hf_to_gguf.py").exists() or (p / "convert-hf-to-gguf.py").exists()
    if not has_convert:
        return CheckResult(
            "llama.cpp",
            False,
            f"convert_hf_to_gguf.py not found in {p}",
            "Reclone or update your llama.cpp checkout.",
        )
    quant_bin = _find_quantize_binary(p)
    if quant_bin is None:
        return CheckResult(
            "llama.cpp",
            False,
            f"llama-quantize binary not built under {p}",
            f"Build it:\n  cd {p}\n  cmake -B build\n"
            f"  cmake --build build --config Release --target llama-quantize",
        )
    return CheckResult("llama.cpp", True, f"convert + quantize ready ({quant_bin.name})")


def check_model_repo_clone(root: Path) -> CheckResult:
    """Local clone of brijeshch8482/openbro-model exists with LFS."""
    repo_dir = root / "openbro-model"
    if not repo_dir.exists():
        return CheckResult(
            "openbro-model clone",
            False,
            f"not at {repo_dir}",
            "Clone it:\n"
            "  gh repo create brijeshch8482/openbro-model --public\n"
            f"  git clone https://github.com/brijeshch8482/openbro-model {repo_dir}\n"
            f"  cd {repo_dir}\n"
            '  git lfs install && git lfs track "*.gguf"\n'
            "  git add .gitattributes && git commit -m init && git push",
        )
    if not (repo_dir / ".git").exists():
        return CheckResult(
            "openbro-model clone",
            False,
            f"{repo_dir} exists but is not a git repo",
            f"Remove and reclone: rm -rf {repo_dir}",
        )
    return CheckResult("openbro-model clone", True, f"at {repo_dir}")


def check_base_model_cached() -> CheckResult:
    """Llama-3.2-1B-Instruct downloaded to HuggingFace cache."""
    try:
        from huggingface_hub import HfApi

        api = HfApi()
        try:
            api.model_info("meta-llama/Llama-3.2-1B-Instruct")
        except Exception:
            return CheckResult(
                "Base model access",
                False,
                "meta-llama/Llama-3.2-1B-Instruct not reachable",
                "Accept the license: "
                "https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct\n"
                "Click 'Accept and access repository' (one-time gate).",
            )
    except ImportError:
        return CheckResult(
            "Base model access",
            False,
            "huggingface_hub not installed",
            "Install training extras first.",
        )
    return CheckResult("Base model access", True, "meta-llama/Llama-3.2-1B-Instruct accessible")


def run_all_checks(root: Path) -> list[CheckResult]:
    """Run every pre-flight check in order."""
    return [
        check_python_packages(),
        check_cuda(),
        check_huggingface_login(),
        check_gh_cli(),
        check_llama_cpp(),
        check_model_repo_clone(root),
        check_base_model_cached(),
    ]
