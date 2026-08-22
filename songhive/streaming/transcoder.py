"""
Audio transcoder using ffmpeg.
"""

import asyncio
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class TranscodeResult:
    """Result of a transcoding operation."""

    output_path: Path
    mimetype: str
    duration: Optional[float] = None


class TranscoderError(Exception):
    """Error during transcoding."""


class Transcoder:
    """
    Audio transcoder wrapping ffmpeg.

    Supports conversion between common audio formats:
    MP3, OGG (Vorbis), FLAC, AAC, Opus.
    """

    FORMAT_MAP = {
        "mp3": {"codec": "libmp3lame", "ext": "mp3", "mimetype": "audio/mpeg"},
        "ogg": {"codec": "libvorbis", "ext": "ogg", "mimetype": "audio/ogg"},
        "flac": {"codec": "flac", "ext": "flac", "mimetype": "audio/flac"},
        "aac": {"codec": "aac", "ext": "m4a", "mimetype": "audio/mp4"},
        "opus": {"codec": "libopus", "ext": "opus", "mimetype": "audio/opus"},
    }

    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"

    async def transcode(
        self,
        input_path: Path,
        output_format: str,
        output_dir: Optional[Path] = None,
        bitrate: Optional[str] = None,
    ) -> TranscodeResult:
        """
        Transcode an audio file to the specified format.

        :param input_path: Path to the input audio file.
        :param output_format: Target format (mp3, ogg, flac, aac, opus).
        :param output_dir: Directory for the output file (defaults to input dir).
        :param bitrate: Target bitrate (e.g., "192k"). None for default.
        :returns: TranscodeResult with the output path and mimetype.
        :raises TranscoderError: If transcoding fails.
        """
        if output_format not in self.FORMAT_MAP:
            raise TranscoderError(f"Unsupported format: {output_format}")

        fmt = self.FORMAT_MAP[output_format]
        if not input_path.exists():
            raise TranscoderError(f"Input file does not exist: {input_path}")

        if output_dir is None:
            output_dir = input_path.parent

        output_path = output_dir / f"{input_path.stem}.{fmt['ext']}"

        cmd = [
            self.ffmpeg_path,
            "-i",
            str(input_path),
            "-c:a",
            fmt["codec"],
            "-y",  # overwrite
        ]

        if bitrate:
            cmd.extend(["-b:a", bitrate])

        cmd.append(str(output_path))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise TranscoderError(f"ffmpeg exited with code {proc.returncode}: " f"{stderr.decode().strip()}")

        return TranscodeResult(
            output_path=output_path,
            mimetype=fmt["mimetype"],
        )

    async def stream(
        self,
        input_path: Path,
        output_format: str,
        bitrate: Optional[str] = None,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        """
        Transcode an audio file on the fly and yield the output in chunks.

        :param input_path: Path to the input audio file.
        :param output_format: Target format (mp3, ogg, flac, aac, opus).
        :param bitrate: Target bitrate (e.g., "192k"). None for ffmpeg default.
        :param chunk_size: Number of bytes to read from ffmpeg stdout per chunk.
        :raises TranscoderError: If the format is unsupported, the input is missing,
            or ffmpeg exits with a non-zero status.
        """
        if output_format not in self.FORMAT_MAP:
            raise TranscoderError(f"Unsupported format: {output_format}")

        fmt = self.FORMAT_MAP[output_format]
        if not input_path.exists():
            raise TranscoderError(f"Input file does not exist: {input_path}")

        cmd = [
            self.ffmpeg_path,
            "-i",
            str(input_path),
            "-c:a",
            fmt["codec"],
            "-f",
            fmt["ext"],
            "-y",
        ]
        if bitrate:
            cmd.extend(["-b:a", bitrate])
        cmd.append("-")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None
        try:
            while True:
                chunk = await proc.stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
            await proc.wait()
            if proc.returncode != 0:
                stderr = await proc.stderr.read()
                raise TranscoderError(f"ffmpeg exited with code {proc.returncode}: " f"{stderr.decode().strip()}")
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
