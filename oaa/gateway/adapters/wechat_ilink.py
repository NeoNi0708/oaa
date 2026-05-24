"""WeChat iLink adapter — powered by official wechatbot-sdk."""
import asyncio
import base64
import io
import json
import sys
from typing import Any

from wechatbot import WeChatBot, IncomingMessage, decrypt_aes_ecb, decode_aes_key
from wechatbot.errors import ApiError, MediaError
from wechatbot.protocol import ILinkApi, CDN_BASE_URL, DEFAULT_BASE_URL, MediaType, MessageItemType

# build_cdn_upload_url is a static method on ILinkApi, referenced as ILinkApi.build_cdn_upload_url()

from ..gateway import Message

_IMG_MAX_SIZE = 768  # max width/height for multimodal LLM (keeps tokens reasonable)


def _resize_image(data: bytes) -> bytes:
    """Resize image to fit within _IMG_MAX_SIZE, return as JPEG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if w > _IMG_MAX_SIZE or h > _IMG_MAX_SIZE:
        scale = _IMG_MAX_SIZE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue()

# iLink API endpoints (from wechatbot-sdk protocol.py)
ILINK_QR_FETCH = "/ilink/bot/get_bot_qrcode"
ILINK_QR_POLL = "/ilink/bot/get_qrcode_status"


class WeChatILinkAdapter:
    """iLink adapter — QR login, message polling, sending via wechatbot SDK."""

    API_BASE = DEFAULT_BASE_URL
    UPLOAD_RET_REJECTED = -2  # server-side upload rejection (not session expiry)

    def __init__(self, token: str = "", bot_id: str = "", base_url: str = "",
                 ilink_user_id: str = ""):
        self.token = token
        self.bot_id = bot_id
        self.base_url = base_url or self.API_BASE
        self._bot_user_id = ilink_user_id
        self._running = False
        self.gateway = None
        self._seen_msg_ids: set[int] = set()
        self._context_tokens: dict[str, str] = {}
        self._cursor = ""

        # Upload health tracking: starts True, set to False on ret=-2
        self._upload_available = True

        # Use the SDK's raw API client
        self._api = ILinkApi()

        # WeChatBot instance for message parsing & sending
        self._bot = WeChatBot(base_url=self.base_url)

        # Cache: wxid -> typing_ticket (avoids fetching config on every message)
        self._typing_tickets: dict[str, str] = {}

        sys.stderr.write(f"[WeChat] Adapter created: token={'SET' if token else 'EMPTY'}, base_url={'SET' if base_url else 'EMPTY'}\n")
        sys.stderr.flush()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token and self.base_url)

    @property
    def upload_available(self) -> bool:
        return self._upload_available

    # ------------------------------------------------------------------
    # QR-code login flow
    # ------------------------------------------------------------------

    def get_qrcode(self) -> dict:
        """Get QR code for WeChat scan login."""
        import requests
        try:
            resp = requests.get(
                f"{self.API_BASE}{ILINK_QR_FETCH}",
                params={"bot_type": "3"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {"error": str(e)}

        if data.get("ret") == 0:
            qr_url = data.get("qrcode_img_content", "")
            qrcode_id = data.get("qrcode", "")
            try:
                import qrcode as qrcode_lib
                import io
                img = qrcode_lib.make(qr_url)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            except ImportError:
                qr_data_uri = qr_url
            return {"qrcode_url": qr_data_uri, "qrcode_id": qrcode_id}
        return {"error": data.get("msg", "获取二维码失败")}

    def poll_qrcode_status(self, qrcode_id: str, timeout: int = 8) -> dict:
        """Poll QR code scan status."""
        import requests
        try:
            resp = requests.get(
                f"{self.API_BASE}{ILINK_QR_POLL}",
                params={"qrcode": qrcode_id},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return {"status": "error", "msg": str(e)}

        status = data.get("status", "waiting")
        if status in ("scaned", "scanned"):
            return {"status": "scanned"}
        if status == "confirmed":
            token = data.get("bot_token", "")
            if token:
                self.token = token
                self.bot_id = data.get("ilink_bot_id", "")
                self.base_url = data.get("baseurl", data.get("base_url", self.base_url))
                self._bot_user_id = data.get("ilink_user_id", "")
                self._bot._base_url = self.base_url
            return {
                "status": "confirmed",
                "bot_token": self.token,
                "base_url": self.base_url,
                "ilink_bot_id": self.bot_id,
                "ilink_user_id": self._bot_user_id,
            }
        if status == "expired":
            return {"status": "expired"}
        return {"status": "waiting"}

    # ------------------------------------------------------------------
    # Message sending
    # ------------------------------------------------------------------

    async def send_message(
        self, to_wxid: str, content: str, context_token: str = ""
    ) -> dict:
        """Send a WeChat text message via the iLink API."""
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not authenticated"}

        ctx = context_token or self._context_tokens.get(to_wxid, "")
        try:
            msg = self._api.build_text_message(to_wxid, ctx, content)
            await self._api.send_message(self.base_url, self.token, msg)
            return {"status": "success"}
        except ApiError as e:
            return {"status": "error", "msg": str(e)}

    async def send_file(
        self, to_wxid: str, file_path: str, context_token: str = ""
    ) -> dict:
        """Send a local file to a WeChat contact via CDN upload + media message.

        Flow: read file → AES-128-ECB encrypt → CDN upload → build media msg → send.
        Supports images, documents, videos (whatever WeChat's CDN accepts).
        """
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not authenticated"}

        if not self._upload_available:
            return {"status": "error", "ret": self.UPLOAD_RET_REJECTED,
                    "msg": "文件上传功能不可用，请重新扫码登录"}

        import hashlib, os, secrets
        from wechatbot import encrypt_aes_ecb
        from wechatbot.crypto import encode_aes_key_base64, encode_aes_key_hex

        ctx = context_token or self._context_tokens.get(to_wxid, "")

        # 1. Read file
        if not os.path.isfile(file_path):
            return {"status": "error", "msg": f"File not found: {file_path}"}
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        file_name = os.path.basename(file_path)
        raw_size = len(raw_bytes)
        raw_md5 = hashlib.md5(raw_bytes).hexdigest()

        # 2. Generate AES key (16 bytes)
        aes_key = secrets.token_bytes(16)

        # 3. Encrypt with AES-128-ECB + PKCS7
        ciphertext = encrypt_aes_ecb(raw_bytes, aes_key)
        enc_size = len(ciphertext)

        # 4. Determine media type from extension
        ext = os.path.splitext(file_name)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            media_type = MediaType.IMAGE
        elif ext in (".mp4", ".mov", ".avi", ".mkv", ".wmv"):
            media_type = MediaType.VIDEO
        elif ext in (".mp3", ".wav", ".aac", ".ogg", ".wma"):
            media_type = MediaType.VOICE
        else:
            media_type = MediaType.FILE

        # Match SDK: random hex filekey (no prefix)
        filekey = secrets.token_bytes(16).hex()

        # 5. Get CDN upload URL
        try:
            upload_info = await self._api.get_upload_url(
                self.base_url, self.token,
                filekey=filekey,
                media_type=media_type.value,
                to_user_id=to_wxid,
                rawsize=raw_size,
                rawfilemd5=raw_md5,
                filesize=enc_size,
                no_need_thumb=True,
                aeskey=encode_aes_key_hex(aes_key),
            )
        except ApiError as e:
            if e.errcode == self.UPLOAD_RET_REJECTED:
                self._upload_available = False
                return {"status": "error", "ret": e.errcode,
                        "msg": f"微信上传接口拒绝(ret={e.errcode})，文件上传功能不可用，请重新扫码登录"}
            return {"status": "error", "msg": f"获取上传URL失败: {e}"}
        except Exception as e:
            return {"status": "error", "msg": f"获取上传URL失败: {e}"}

        # Match SDK: field name is "upload_param" in the response
        upload_param = upload_info.get("upload_param")
        if not upload_param:
            return {"status": "error", "msg": "Upload URL response missing upload_param"}

        # 6. Build CDN URL and upload (use SDK's upload_to_cdn with built-in retry)
        cdn_url = ILinkApi.build_cdn_upload_url(CDN_BASE_URL, upload_param, filekey)
        try:
            encrypt_query_param = await self._api.upload_to_cdn(cdn_url, ciphertext)
        except Exception as e:
            return {"status": "error", "msg": f"CDN upload failed: {e}"}

        # 7. Build media message item (matching SDK per-type format)
        aes_key_b64 = encode_aes_key_base64(aes_key)
        media_dict = {
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }

        type_map = {
            MediaType.IMAGE: (MessageItemType.IMAGE, "image_item", {
                "media": media_dict,
                "mid_size": enc_size,
            }),
            MediaType.VIDEO: (MessageItemType.VIDEO, "video_item", {
                "media": media_dict,
                "video_size": enc_size,
            }),
            MediaType.VOICE: (MessageItemType.VOICE, "voice_item", {
                "media": media_dict,
            }),
            MediaType.FILE: (MessageItemType.FILE, "file_item", {
                "file_name": file_name,
                "media": media_dict,
                "md5": raw_md5,
                "len": str(raw_size),
            }),
        }
        item_type, item_key, item_body = type_map[media_type]
        item = {"type": item_type, item_key: item_body}

        # 8. Build and send media message
        try:
            msg = self._api.build_media_message(to_wxid, ctx, [item])
            await self._api.send_message(self.base_url, self.token, msg)
            return {"status": "success", "file": file_name, "size": raw_size}
        except Exception as e:
            return {"status": "error", "msg": f"Send failed: {e}"}

    async def send_typing(self, to_wxid: str, status: int = 1) -> dict:
        """Send typing indicator (1 = start, 0 = stop).

        Caches the ``typing_ticket`` per user so we don't fetch config on
        every keystroke.  The ticket is obtained from the iLink config API
        and is valid for the duration of the context session.
        """
        if not self.is_authenticated:
            return {"status": "error", "msg": "Not authenticated"}

        ctx = self._context_tokens.get(to_wxid, "")
        if not ctx:
            return {"status": "error", "msg": "No context token"}

        # Use cached ticket if available
        ticket = self._typing_tickets.get(to_wxid)
        if not ticket:
            try:
                config = await self._api.get_config(self.base_url, self.token, to_wxid, ctx)
                ticket = config.get("typing_ticket")
                if ticket:
                    self._typing_tickets[to_wxid] = ticket
            except Exception as e:
                return {"status": "error", "msg": str(e)}

        if not ticket:
            return {"status": "error", "msg": "No typing_ticket"}

        try:
            await self._api.send_typing(self.base_url, self.token, to_wxid, ticket, status)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    # ------------------------------------------------------------------
    # CDN media download
    # ------------------------------------------------------------------

    async def _download_image(self, encrypt_query_param: str, aes_key: str) -> bytes | None:
        """Download and decrypt a CDN image."""
        import aiohttp
        from urllib.parse import quote

        url = f"{CDN_BASE_URL}/download?encrypted_query_param={quote(encrypt_query_param)}"
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status >= 400:
                        return None
                    ciphertext = await resp.read()
        except Exception:
            return None

        if not aes_key:
            return ciphertext
        try:
            key_bytes = decode_aes_key(aes_key)
            return decrypt_aes_ecb(ciphertext, key_bytes)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Message polling
    # ------------------------------------------------------------------

    async def get_updates(self) -> tuple[list[dict], str]:
        """Long-poll for new messages via wechatbot SDK API."""
        if not self.is_authenticated:
            return [], self._cursor

        try:
            updates = await self._api.get_updates(self.base_url, self.token, self._cursor)
        except Exception:
            return [], self._cursor

        new_cursor = updates.get("get_updates_buf", self._cursor)
        raw_msgs = updates.get("msgs", [])

        messages = []
        for raw in raw_msgs:
            if raw.get("message_type") != 1:  # 1 = USER message
                continue

            msg_id = raw.get("message_id", 0)
            if msg_id and msg_id in self._seen_msg_ids:
                continue
            if msg_id:
                self._seen_msg_ids.add(msg_id)
                if len(self._seen_msg_ids) > 1000:
                    self._seen_msg_ids.clear()

            from_user_id = raw.get("from_user_id", "")
            context_token = raw.get("context_token", "")
            if from_user_id and context_token:
                self._context_tokens[from_user_id] = context_token

            # Parse message using wechatbot SDK's IncomingMessage
            incoming = self._bot._parse_message(raw)
            if incoming is None:
                continue

            # Build content: text + embedded images for multimodal
            parts = []
            if incoming.text:
                parts.append(incoming.text)

            # Download images and embed as base64 data URIs
            image_b64s = []
            for img in incoming.images:
                if img.media and img.media.encrypt_query_param:
                    img_bytes = await self._download_image(
                        img.media.encrypt_query_param,
                        img.aes_key or img.media.aes_key,
                    )
                    if img_bytes:
                        try:
                            img_bytes = _resize_image(img_bytes)
                        except Exception:
                            pass
                        b64 = base64.b64encode(img_bytes).decode()
                        uri = f"data:image/jpeg;base64,{b64}"
                        image_b64s.append(uri)
                        parts.append(f"[图片: {uri}]")

            content = "\n".join(parts).strip()
            if not content:
                continue

            messages.append({
                "from_user_id": from_user_id,
                "content": content,
                "context_token": context_token,
                "message_id": msg_id,
                "images": image_b64s,
            })

        return messages, new_cursor

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def start_polling(self):
        """Long-poll loop for incoming WeChat messages."""
        self._running = True

        while self._running:
            msgs, new_cursor = await self.get_updates()
            if new_cursor:
                self._cursor = new_cursor

            for msg_data in msgs:
                from_wxid = msg_data["from_user_id"]
                content = msg_data["content"]
                ctx_token = msg_data["context_token"]

                # Periodically refresh "对方正在输入..." while the agent processes.
                # WeChat's typing indicator auto-stops after ~5 s, so we keep a
                # background task that re-sends it every 5 s until processing finishes.
                typing_task = asyncio.ensure_future(self._keep_typing(from_wxid))

                msg = Message(
                    source="wechat",
                    user_id=from_wxid,
                    content=content,
                    metadata={
                        "msg_data": msg_data,
                        "context_token": ctx_token,
                    },
                    images=msg_data.get("images", []),
                )

                if self.gateway is not None:
                    try:
                        async for chunk in self.gateway.incoming_message(msg):
                            if chunk["type"] == "done":
                                reply = chunk.get("content", "")
                                if reply:
                                    await self.send_message(from_wxid, reply, ctx_token)
                    except Exception as e:
                        sys.stderr.write(f"[WeChat] Error processing: {e}\n")

                # Stop typing indicator
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
                await self.send_typing(from_wxid, status=0)

            await asyncio.sleep(1)

    async def _keep_typing(self, to_wxid: str):
        """Refresh typing indicator every 5 s until cancelled."""
        try:
            while True:
                result = await self.send_typing(to_wxid, status=1)
                if result.get("status") == "error":
                    sys.stderr.write(f"[WeChat] typing indicator error for {to_wxid}: {result.get('msg')}\n")
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def stop_polling(self):
        self._running = False

    async def start(self):
        await self.start_polling()

    async def stop(self):
        self.stop_polling()
