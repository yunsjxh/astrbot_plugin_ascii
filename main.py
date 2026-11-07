"""AstrBot plugin that turns an attached image into ASCII art."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage, ImageFont as PILImageFont

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain
from astrbot.api.star import Context, Star, StarTools, register

PLUGIN_NAME = "astrbot_plugin_ascii"
DEFAULT_GENERATOR_DIR = "/opt/ASCII-generator"
DEFAULT_PYTHON = "/opt/AstrBot/venv/bin/python"
DEFAULT_RECENT_TTL = 60
RECENT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
RECENT_IMAGE_MAX_ENTRIES = 256
AVATAR_URL_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={uin}&s=640"
AVATAR_FETCH_TIMEOUT = 10
QQ_ID_RE = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")


@register(
    PLUGIN_NAME,
    "yunsjxh",
    "Convert an attached image or QQ avatar into ASCII art",
    "1.1.0",
    "https://github.com/yunsjxh/astrbot_plugin_ascii",
)
class ASCIIArtPlugin(Star):
    """Handle the /ascii command and reply with a generated ASCII-art image."""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        # Fall back to built-in defaults until a config is saved in the WebUI.
        self.config = config or {}
        self.output_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Fixed paths avoid accumulating temporary files.
        self.output_path = self.output_dir / "ascii_result.jpg"
        self.source_path = self.output_dir / "ascii_source.jpg"
        self.recent_path = self.output_dir / "ascii_recent_input.jpg"
        self._recent_images: dict[str, tuple[float, bytes]] = {}
        self._lock = asyncio.Lock()

    def _recent_ttl(self) -> int:
        """Return the look-back window in seconds; 0 disables the cache."""
        try:
            return max(
                0,
                min(int(self.config.get("recent_image_ttl", DEFAULT_RECENT_TTL)), 600),
            )
        except (TypeError, ValueError):
            return DEFAULT_RECENT_TTL

    @staticmethod
    def _cache_key(event: AstrMessageEvent) -> str:
        """Scope cached images to one sender inside one conversation."""
        sender = event.get_sender_id() or "unknown"
        session = event.get_session_id() or event.get_group_id() or "dm"
        return f"{session}:{sender}"

    def _remember(self, key: str, data: bytes) -> None:
        """Store the latest image bytes and prune expired / excess entries."""
        ttl = self._recent_ttl()
        if ttl <= 0 or not data or len(data) > RECENT_IMAGE_MAX_BYTES:
            return
        now = time.monotonic()
        cache = self._recent_images
        for cached_key, (stored_at, _) in list(cache.items()):
            if now - stored_at > ttl:
                cache.pop(cached_key, None)
        cache[key] = (now, data)
        while len(cache) > RECENT_IMAGE_MAX_ENTRIES:
            oldest = min(cache, key=lambda item: cache[item][0])
            cache.pop(oldest, None)

    def _recent(self, key: str) -> bytes | None:
        """Return cached image bytes only while they are inside the TTL."""
        ttl = self._recent_ttl()
        if ttl <= 0:
            return None
        item = self._recent_images.get(key)
        if item is None:
            return None
        stored_at, data = item
        if time.monotonic() - stored_at > ttl:
            self._recent_images.pop(key, None)
            return None
        return data

    @staticmethod
    async def _image_bytes(image: Image) -> bytes:
        """Resolve an Image component to bytes regardless of its origin."""
        return base64.b64decode(await image.convert_to_base64())

    @staticmethod
    def _target_uin(event: AstrMessageEvent, components: list) -> str:
        """Resolve an @mention or an explicit QQ number from the command."""
        self_id = str(event.get_self_id() or "")
        for seg in components:
            if isinstance(seg, At):
                uin = str(getattr(seg, "qq", "") or "").strip()
                if (
                    uin
                    and uin.isdigit()
                    and uin.lower() != "all"
                    and uin != self_id
                ):
                    return uin
        # Plain-text fallback: "/ascii 123456" or a manually typed "@123456".
        text = str(getattr(event, "message_str", "") or "")
        if not text:
            text = " ".join(
                str(seg.text or "") for seg in components if isinstance(seg, Plain)
            )
        for candidate in QQ_ID_RE.findall(text):
            if candidate != self_id:
                return candidate
        return ""

    @staticmethod
    def _avatar_url(uin: str) -> str:
        """Return the 640px QQ avatar URL for a numeric uin."""
        return AVATAR_URL_TEMPLATE.format(uin=uin)

    async def _fetch_avatar(self, uin: str) -> bytes | None:
        """Download an avatar through AstrBot's media resolver."""
        try:
            return await asyncio.wait_for(
                self._image_bytes(Image(file=self._avatar_url(uin))),
                timeout=AVATAR_FETCH_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("Failed to fetch QQ avatar for %s: %s", uin, exc)
            return None

    @staticmethod
    def _aspect_factor(generator_dir: Path) -> float:
        """Return the width stretch introduced by the upstream scripts.

        The generator samples with ``cell_height = 2 * cell_width`` but draws
        lines at ``char_height`` spacing, so the output aspect ratio is
        ``2 * char_width / char_height`` times the source ratio. Deriving the
        factor from the font keeps the correction valid if the font changes.
        """
        try:
            font = PILImageFont.truetype(
                str(generator_dir / "fonts" / "DejaVuSansMono-Bold.ttf"), size=20
            )
            left, top, right, bottom = font.getbbox("A")
            factor = 2.0 * (right - left) / (bottom - top)
            return factor if 0.5 < factor < 4.0 else 1.6
        except Exception as exc:
            logger.warning("Failed to derive aspect factor, using 1.6: %s", exc)
            return 1.6

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def remember_recent_image(self, event: AstrMessageEvent):
        """Passively remember the last image each user sent in this session."""
        ttl = self._recent_ttl()
        sender = event.get_sender_id()
        if ttl <= 0 or not sender:
            return
        self_id = event.get_self_id()
        if self_id and sender == self_id:
            return
        components = getattr(event.message_obj, "message", None) or []
        image = next((seg for seg in components if isinstance(seg, Image)), None)
        if image is None:
            return
        try:
            data = await self._image_bytes(image)
        except Exception as exc:
            logger.debug("Failed to cache a recent image: %s", exc)
            return
        self._remember(self._cache_key(event), data)

        # Keep this an async generator so AstrBot's handler wrapper can
        # iterate it, while never emitting a reply for ordinary messages.
        if False:  # pragma: no cover
            yield

    @filter.command("ascii", alias={"字符画"})
    async def ascii_art(self, event: AstrMessageEvent):
        """Convert an attached image, an avatar, or the recent image to ASCII art.

        Args:
            event: The incoming message event, optionally carrying an image,
                an @mention, or a QQ number.

        Yields:
            A message event result with the generated picture, or a short
            hint when no usable image can be found.
        """
        key = self._cache_key(event)
        components = getattr(event.message_obj, "message", None) or []
        image = next((seg for seg in components if isinstance(seg, Image)), None)
        # An attached image always wins; otherwise an @mention / QQ number
        # selects an avatar before falling back to the recent-image cache.
        uin = self._target_uin(event, components) if image is None else ""

        if image is not None:
            try:
                source_bytes = await self._image_bytes(image)
            except Exception as exc:
                logger.error("Failed to resolve incoming image: %s", exc)
                yield event.plain_result("图片读取失败了，换一张试试？")
                return
            # A newly attached image also becomes the newest cached one.
            self._remember(key, source_bytes)
        elif uin:
            source_bytes = await self._fetch_avatar(uin)
            if not source_bytes:
                yield event.plain_result(
                    f"没抓到 {uin} 的头像，确认一下 QQ 号或稍后再试～"
                )
                return
            self._remember(key, source_bytes)
        else:
            source_bytes = self._recent(key)
            if source_bytes is None:
                ttl = self._recent_ttl()
                if ttl > 0:
                    yield event.plain_result(
                        f"最近 {ttl} 秒内没收到你发的图片～先发一张图再单发 /ascii，"
                        "也可以 /ascii @某人 或 /ascii QQ号 直接抓头像。"
                    )
                else:
                    yield event.plain_result(
                        "把图片和 /ascii 一起发，或用 /ascii @某人 / QQ号 抓头像～"
                    )
                return

        if not source_bytes:
            yield event.plain_result("图片是空的，换一张试试？")
            return

        try:
            self.recent_path.write_bytes(source_bytes)
        except OSError as exc:
            logger.error("Failed to stage source image: %s", exc)
            yield event.plain_result("图片暂存失败了，换一张试试？")
            return
        source = str(self.recent_path)

        generator_dir = Path(self.config.get("generator_dir", DEFAULT_GENERATOR_DIR))
        python_bin = self.config.get("python_path", DEFAULT_PYTHON)
        color = bool(self.config.get("color", False))
        white_bg = bool(self.config.get("white_background", False))
        cols = max(20, min(int(self.config.get("num_cols", 100)), 300))
        timeout = max(5, int(self.config.get("timeout", 30)))
        script = "img2img_color.py" if color else "img2img.py"
        keep_size = bool(self.config.get("keep_original_size", False))
        fix_aspect = bool(self.config.get("fix_aspect_ratio", True))

        target_size = None
        if fix_aspect:
            try:
                with PILImage.open(source) as source_image:
                    src_size = source_image.size
                    stretched = source_image.convert("RGB").resize(
                        (
                            src_size[0],
                            max(1, round(src_size[1] * self._aspect_factor(generator_dir))),
                        ),
                        PILImage.Resampling.LANCZOS,
                    )
                stretched.save(self.source_path, format="JPEG", quality=95)
                source = str(self.source_path)
                if keep_size:
                    target_size = src_size
            except Exception as exc:
                logger.warning("Failed to pre-stretch source for aspect fix: %s", exc)
        elif keep_size:
            try:
                with PILImage.open(source) as source_image:
                    target_size = source_image.size
            except Exception as exc:
                logger.warning("Failed to read source image size: %s", exc)

        if not (generator_dir / script).is_file():
            logger.error("ASCII generator script missing: %s", generator_dir / script)
            yield event.plain_result("转换脚本没找到，请联系管理员检查 /opt/ASCII-generator。")
            return

        cmd = [
            python_bin,
            script,
            "--input",
            source,
            "--output",
            str(self.output_path),
            "--num_cols",
            str(cols),
            "--language",
            "english",
            "--background",
            "white" if white_bg else "black",
        ]

        proc = None
        async with self._lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(generator_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                if proc is not None:
                    proc.kill()
                    await proc.wait()
                logger.error("ASCII conversion timed out after %ss", timeout)
                yield event.plain_result("图片太大或转换超时了，调小列数再试试～")
                return
            except Exception as exc:
                logger.error("Failed to launch ASCII converter: %s", exc)
                yield event.plain_result("转换程序启动失败，请联系管理员。")
                return

            if proc.returncode != 0:
                logger.error(
                    "ASCII converter exited %s: %s",
                    proc.returncode,
                    stderr.decode("utf-8", "replace")[-500:],
                )
                yield event.plain_result("转换失败了，换张图试试？")
                return
            if (
                not self.output_path.is_file()
                or self.output_path.stat().st_size == 0
            ):
                logger.error("ASCII converter produced no output")
                yield event.plain_result("没有生成图片，换张图试试？")
                return
            result_bytes = self.output_path.read_bytes()

        if target_size is not None:
            try:
                with PILImage.open(BytesIO(result_bytes)) as art_image:
                    resized = art_image.convert("RGB").resize(
                        target_size, PILImage.Resampling.LANCZOS
                    )
                buffer = BytesIO()
                resized.save(buffer, format="JPEG", quality=90)
                result_bytes = buffer.getvalue()
            except Exception as exc:
                logger.warning(
                    "Failed to resize ASCII art to %s: %s", target_size, exc
                )

        # aiocqhttp encodes Image components as base64 before sending, so a
        # byte payload works even though NapCat runs in a separate container.
        yield event.chain_result([Image.fromBytes(result_bytes)])
