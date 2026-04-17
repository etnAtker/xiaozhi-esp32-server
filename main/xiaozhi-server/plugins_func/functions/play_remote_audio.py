import os
import asyncio
import tempfile
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from pydub import AudioSegment

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from core.utils.dialogue import Message
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from core.handle.sendAudioHandle import send_tts_message
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
}

play_remote_audio_function_desc = {
    "type": "function",
    "function": {
        "name": "play_remote_audio",
        "description": "播放一段远程音频，参数是可直接访问的音频 URL 链接。",
        "parameters": {
            "type": "object",
            "properties": {
                "audio_url": {
                    "type": "string",
                    "description": "远程音频的 HTTP/HTTPS 链接，例如 https://example.com/demo.mp3",
                }
            },
            "required": ["audio_url"],
        },
    },
}


@register_function(
    "play_remote_audio", play_remote_audio_function_desc, ToolType.SYSTEM_CTL
)
def play_remote_audio(conn: "ConnectionHandler", audio_url: str):
    try:
        if not _is_valid_audio_url(audio_url):
            return ActionResponse(
                action=Action.RESPONSE,
                result="无效的音频链接",
                response="请提供可访问的 HTTP 或 HTTPS 音频链接",
            )

        if not conn.loop.is_running():
            conn.logger.bind(tag=TAG).error("事件循环未运行，无法提交任务")
            return ActionResponse(
                action=Action.RESPONSE, result="系统繁忙", response="请稍后再试"
            )

        task = conn.loop.create_task(handle_remote_audio_command(conn, audio_url))

        def handle_done(future):
            try:
                future.result()
                conn.logger.bind(tag=TAG).info("远程音频播放任务已结束")
            except Exception as exc:
                conn.logger.bind(tag=TAG).error(f"远程音频播放失败: {exc}")

        task.add_done_callback(handle_done)

        return ActionResponse(
            action=Action.NONE,
            result="指令已接收",
            response="正在为您播放远程音频",
        )
    except Exception as exc:
        conn.logger.bind(tag=TAG).error(f"处理远程音频播放错误: {exc}")
        return ActionResponse(
            action=Action.RESPONSE,
            result=str(exc),
            response="播放远程音频时出错了",
        )


def _is_valid_audio_url(audio_url: str) -> bool:
    try:
        parsed = urlparse(audio_url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _guess_audio_extension(audio_url: str, content_type: str | None) -> str:
    parsed = urlparse(audio_url)
    path = unquote(parsed.path or "")
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix

    if content_type:
        mime_type = content_type.split(";", 1)[0].strip().lower()
        guessed = CONTENT_TYPE_EXTENSIONS.get(mime_type)
        if guessed:
            return guessed

    return ".mp3"


def _download_audio_file(conn: "ConnectionHandler", audio_url: str) -> str:
    plugins_config = conn.config.get("plugins", {}).get("play_remote_audio", {})
    temp_dir = plugins_config.get("temp_dir")
    request_timeout = int(plugins_config.get("request_timeout", 30))
    max_file_size = int(plugins_config.get("max_file_size", 50 * 1024 * 1024))

    if temp_dir:
        os.makedirs(temp_dir, exist_ok=True)
    else:
        temp_dir = tempfile.gettempdir()

    conn.logger.bind(tag=TAG).info(f"开始下载远程音频: {audio_url}")
    with requests.get(audio_url, stream=True, timeout=request_timeout) as response:
        response.raise_for_status()
        extension = _guess_audio_extension(
            audio_url, response.headers.get("Content-Type")
        )

        with tempfile.NamedTemporaryFile(
            prefix="xiaozhi_remote_audio_",
            suffix=extension,
            dir=temp_dir,
            delete=False,
        ) as temp_file:
            total_size = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total_size += len(chunk)
                if total_size > max_file_size:
                    raise ValueError("远程音频文件过大，已超过下载限制")
                temp_file.write(chunk)
            temp_path = temp_file.name

    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
        raise ValueError("远程音频下载失败或文件为空")

    conn.logger.bind(tag=TAG).info(f"远程音频下载完成: {temp_path}")
    return temp_path


def _build_play_prompt(audio_url: str) -> str:
    path = unquote(urlparse(audio_url).path or "")
    file_name = Path(path).stem.strip()
    if file_name:
        return f"正在为您播放音频《{file_name}》"
    return "正在为您播放远程音频"


def _estimate_audio_duration(audio_path: str) -> float:
    audio = AudioSegment.from_file(audio_path)
    return max(len(audio) / 1000.0, 1.0)


async def _cleanup_temp_file_later(
    conn: "ConnectionHandler", audio_path: str, delay_seconds: float
):
    try:
        await asyncio.sleep(delay_seconds)
        if os.path.exists(audio_path):
            os.remove(audio_path)
            conn.logger.bind(tag=TAG).info(f"已清理远程音频临时文件: {audio_path}")
    except Exception as exc:
        conn.logger.bind(tag=TAG).warning(
            f"清理远程音频临时文件失败: {audio_path}, error={exc}"
        )


async def _wait_current_playback_finished(conn: "ConnectionHandler"):
    conn.logger.bind(tag=TAG).info("等待当前播报结束后再播放远程音频")

    plugins_config = conn.config.get("plugins", {}).get("play_remote_audio", {})
    wait_timeout = int(
        plugins_config.get(
            "wait_current_playback_timeout",
            conn.config.get("tool_call_timeout", 30),
        )
    )
    poll_interval = float(plugins_config.get("wait_poll_interval", 0.))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout

    while loop.time() < deadline:
        tts_text_empty = True
        tts_audio_empty = True
        rate_queue_empty = True

        if hasattr(conn, "tts") and conn.tts:
            tts_text_empty = conn.tts.tts_text_queue.empty()
            tts_audio_empty = conn.tts.tts_audio_queue.empty()

        if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
            rate_queue_empty = conn.audio_rate_controller.queue_empty_event.is_set()

        if (
            tts_text_empty
            and tts_audio_empty
            and rate_queue_empty
            and not conn.client_is_speaking
        ):
            conn.logger.bind(tag=TAG).info("当前播报已结束，开始播放远程音频")
            return

        await asyncio.sleep(poll_interval)

    conn.logger.bind(tag=TAG).warning(
        f"等待当前播报结束超时({wait_timeout}s)，将直接尝试播放远程音频"
    )


async def play_remote_audio_file(
    conn: "ConnectionHandler", audio_path: str, audio_url: str
):
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"远程音频文件不存在: {audio_path}")

    text = _build_play_prompt(audio_url)
    conn.sentence_id = uuid.uuid4().hex
    conn.client_abort = False
    await send_tts_message(conn, "start")
    conn.client_is_speaking = True
    conn.dialogue.put(Message(role="assistant", content=text))

    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.FIRST,
            content_type=ContentType.ACTION,
        )
    )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.TEXT,
            content_detail=text,
        )
    )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.FILE,
            content_file=audio_path,
        )
    )
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=conn.sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )

    cleanup_buffer = int(
        conn.config.get("plugins", {}).get("play_remote_audio", {}).get(
            "cleanup_buffer_seconds", 30
        )
    )
    try:
        duration_seconds = await asyncio.to_thread(_estimate_audio_duration, audio_path)
    except Exception as exc:
        conn.logger.bind(tag=TAG).warning(
            f"获取远程音频时长失败，将使用默认清理时间: {exc}"
        )
        duration_seconds = 120

    cleanup_delay = max(duration_seconds + cleanup_buffer, 60)
    conn.loop.create_task(_cleanup_temp_file_later(conn, audio_path, cleanup_delay))


async def handle_remote_audio_command(conn: "ConnectionHandler", audio_url: str):
    audio_path = None
    try:
        download_task = asyncio.create_task(
            asyncio.to_thread(_download_audio_file, conn, audio_url)
        )
        wait_task = asyncio.create_task(_wait_current_playback_finished(conn))

        audio_path, _ = await asyncio.gather(download_task, wait_task)
        await play_remote_audio_file(conn, audio_path, audio_url)
        return True
    except Exception as exc:
        if audio_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass
        conn.logger.bind(tag=TAG).error(f"播放远程音频失败: {exc}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
        raise
