import json
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.sendAudioHandle import sendAudio
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.util import audio_to_data

TAG = __name__
RECORD_PLAY_AUDIO_PATH = "data/music/CA001.mp3"


class RecordTextMessageHandler(TextMessageHandler):
    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.RECORD

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        state = msg_json.get("state")
        if state == "start":
            conn.record_only = True
            conn.start_recording_session()
            await self._play_recording_audio(conn)
        elif state == "stop":
            self._stop_recording_audio(conn)
            await conn.stop_recording_session()
        else:
            conn.logger.bind(tag=TAG).warning(f"未知录音状态: {state}")

    async def _play_recording_audio(self, conn: "ConnectionHandler") -> None:
        try:
            opus_packets = await audio_to_data(RECORD_PLAY_AUDIO_PATH)
            await sendAudio(conn, opus_packets)
        except Exception as e:
            conn.logger.bind(tag=TAG).error(
                f"录音播放音频失败，结束录音状态: {RECORD_PLAY_AUDIO_PATH}, error={e}"
            )
            self._stop_recording_audio(conn)
            await conn.stop_recording_session()
            await self._notify_recording_stopped(conn)

    def _stop_recording_audio(self, conn: "ConnectionHandler") -> None:
        if hasattr(conn, "audio_rate_controller") and conn.audio_rate_controller:
            conn.audio_rate_controller.stop_sending()

    async def _notify_recording_stopped(self, conn: "ConnectionHandler") -> None:
        if not conn.websocket:
            return
        message = {
            "type": "record",
            "state": "error",
            "session_id": conn.session_id,
            "reason": "audio_file_error",
        }
        try:
            await conn.websocket.send(json.dumps(message))
        except Exception as e:
            conn.logger.bind(tag=TAG).warning(f"通知客户端结束录音失败: {e}")
