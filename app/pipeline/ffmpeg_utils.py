import asyncio
import json
import logging
import platform
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def hwaccel_args() -> list[str]:
    """Return appropriate FFmpeg hardware acceleration arguments for the current platform.

    Returns platform-specific hwaccel flags:
    - Darwin (macOS): VideoToolbox hardware acceleration
    - Linux: Software decoding (cross-platform compatibility)
    - Windows: DXVA2 hardware acceleration
    - Other: Empty list (software decoding fallback)
    """
    system = platform.system()
    if system == "Darwin":
        return ["-hwaccel", "videotoolbox"]
    elif system == "Windows":
        return ["-hwaccel", "dxva2"]
    else:
        # Linux and other platforms: use software decoding for best compatibility
        return []


async def run(args: list[str]) -> None:
    logger.debug("$ %s", " ".join(args))
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        msg = stderr.decode("utf-8", errors="replace")
        logger.error("%s failed in %.2fs (rc=%d)", args[0], elapsed, proc.returncode)
        raise RuntimeError(f"{args[0]} failed (rc={proc.returncode}): {msg.strip()[-500:]}")
    logger.debug("%s ok in %.2fs", args[0], elapsed)


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='replace')}")
    data = json.loads(stdout)
    duration = float(data["format"]["duration"])
    logger.debug("ffprobe %s: duration=%.2fs", path.name, duration)
    return duration
