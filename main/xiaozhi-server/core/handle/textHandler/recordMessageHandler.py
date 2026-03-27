from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType

TAG = __name__


class RecordTextMessageHandler(TextMessageHandler):
    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.RECORD

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        state = msg_json.get("state")
        if state == "start":
            conn.start_recording_session()
        elif state == "stop":
            await conn.stop_recording_session()
        else:
            conn.logger.bind(tag=TAG).warning(f"未知录音状态: {state}")
