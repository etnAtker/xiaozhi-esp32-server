import json
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional

from config.logger import create_perf_logger


def monotonic_now() -> float:
    return time.monotonic()


def _duration_ms(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start) * 1000, 3)


class ConnectionPerformanceTracker:
    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.RLock()
        self.selected_module = getattr(conn, "selected_module_str", "00000000000000")
        self.perf_logger = create_perf_logger(self.selected_module)
        self.current_turn: Optional[Dict[str, Any]] = None

    def set_selected_module(self, selected_module: str):
        with self.lock:
            self.selected_module = selected_module or "00000000000000"
            self.perf_logger = create_perf_logger(self.selected_module)

    def has_active_turn(self) -> bool:
        with self.lock:
            return self.current_turn is not None

    def start_turn(self, source: str = "unknown", query: Optional[str] = None):
        with self.lock:
            if self.current_turn is not None:
                self._emit_locked("superseded", error="new_turn_started")

            self.current_turn = {
                "turn_id": uuid.uuid4().hex,
                "session_id": self.conn.session_id,
                "sentence_id": None,
                "source": source,
                "status": "running",
                "started_at_wall": datetime.now().isoformat(timespec="milliseconds"),
                "selected_module": self.selected_module,
                "conn_from": (
                    "mqtt_gateway"
                    if getattr(self.conn, "conn_from_mqtt_gateway", False)
                    else "ws"
                ),
                "providers": self._build_provider_info(),
                "query": query,
                "query_length": len(query) if query else 0,
                "query_preview": self._build_preview(query),
                "depth_max": 0,
                "llm_call_count": 0,
                "llm_durations_ms": [],
                "llm_chunk_count": 0,
                "llm_chars": 0,
                "has_tool_call": False,
                "tool_call_count": 0,
                "tool_batch_count": 0,
                "tool_batch_durations_ms": [],
                "tool_calls": [],
                "errors": [],
                "timestamps": {
                    "asr_started_at": None,
                    "asr_finished_at": None,
                    "stt_sent_at": None,
                    "llm_prepare_started_at": None,
                    "llm_started_at": None,
                    "llm_first_chunk_at": None,
                    "llm_first_text_at": None,
                    "llm_finished_at": None,
                    "tool_detected_at": None,
                    "tool_batch_started_at": None,
                    "tool_batch_finished_at": None,
                    "tts_text_queued_at": None,
                    "tts_started_at": None,
                    "tts_first_packet_at": None,
                    "tts_finished_at": None,
                },
            }
            return self.current_turn["turn_id"]

    def ensure_turn(self, source: str = "unknown", query: Optional[str] = None):
        with self.lock:
            if self.current_turn is None:
                self.current_turn = {
                    "turn_id": uuid.uuid4().hex,
                    "session_id": self.conn.session_id,
                    "sentence_id": None,
                    "source": source,
                    "status": "running",
                    "started_at_wall": datetime.now().isoformat(timespec="milliseconds"),
                    "selected_module": self.selected_module,
                    "conn_from": (
                        "mqtt_gateway"
                        if getattr(self.conn, "conn_from_mqtt_gateway", False)
                        else "ws"
                    ),
                    "providers": self._build_provider_info(),
                    "query": query,
                    "query_length": len(query) if query else 0,
                    "query_preview": self._build_preview(query),
                    "depth_max": 0,
                    "llm_call_count": 0,
                    "llm_durations_ms": [],
                    "llm_chunk_count": 0,
                    "llm_chars": 0,
                    "has_tool_call": False,
                    "tool_call_count": 0,
                    "tool_batch_count": 0,
                    "tool_batch_durations_ms": [],
                    "tool_calls": [],
                    "errors": [],
                    "timestamps": {
                        "asr_started_at": None,
                        "asr_finished_at": None,
                        "stt_sent_at": None,
                        "llm_prepare_started_at": None,
                        "llm_started_at": None,
                        "llm_first_chunk_at": None,
                        "llm_first_text_at": None,
                        "llm_finished_at": None,
                        "tool_detected_at": None,
                        "tool_batch_started_at": None,
                        "tool_batch_finished_at": None,
                        "tts_text_queued_at": None,
                        "tts_started_at": None,
                        "tts_first_packet_at": None,
                        "tts_finished_at": None,
                    },
                }
            elif query and not self.current_turn.get("query"):
                self.current_turn["query"] = query
                self.current_turn["query_length"] = len(query)
                self.current_turn["query_preview"] = self._build_preview(query)
            return self.current_turn["turn_id"]

    def update_query(self, query: Optional[str]):
        if not query:
            return
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn(query=query)
            self.current_turn["query"] = query
            self.current_turn["query_length"] = len(query)
            self.current_turn["query_preview"] = self._build_preview(query)

    def attach_sentence(self, sentence_id: str):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["sentence_id"] = sentence_id

    def mark(self, event_name: str, *, first_only: bool = True):
        now = monotonic_now()
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            timestamps = self.current_turn["timestamps"]
            if first_only and timestamps.get(event_name) is not None:
                return
            timestamps[event_name] = now

    def update_depth(self, depth: int):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["depth_max"] = max(
                self.current_turn.get("depth_max", 0), depth
            )

    def add_llm_chunk(self, content: Optional[str]):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["llm_chunk_count"] += 1
            if content:
                self.current_turn["llm_chars"] += len(content)

    def record_llm_call(self, duration_ms: float):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["llm_call_count"] += 1
            self.current_turn["llm_durations_ms"].append(round(duration_ms, 3))

    def mark_tool_detected(self, count: int = 1):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["has_tool_call"] = True
            self.current_turn["tool_call_count"] = max(
                self.current_turn.get("tool_call_count", 0), count
            )
            timestamps = self.current_turn["timestamps"]
            if timestamps.get("tool_detected_at") is None:
                timestamps["tool_detected_at"] = monotonic_now()

    def record_tool_batch(self, duration_ms: float):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["tool_batch_count"] += 1
            self.current_turn["tool_batch_durations_ms"].append(round(duration_ms, 3))

    def mark_tts_text_queued(self):
        self.mark("tts_text_queued_at")

    def record_tool_call(
        self,
        name: str,
        duration_ms: float,
        *,
        action: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None,
    ):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["has_tool_call"] = True
            self.current_turn["tool_call_count"] += 1
            self.current_turn["tool_calls"].append(
                {
                    "name": name,
                    "duration_ms": round(duration_ms, 3),
                    "action": action,
                    "success": success,
                    "error": error,
                }
            )

    def add_error(self, stage: str, error: str):
        with self.lock:
            if self.current_turn is None:
                self.ensure_turn()
            self.current_turn["errors"].append(
                {"stage": stage, "error": str(error), "time": monotonic_now()}
            )

    def finalize(self, status: str = "completed", error: Optional[str] = None):
        with self.lock:
            if self.current_turn is None:
                return
            if error:
                self.current_turn["errors"].append(
                    {"stage": "finalize", "error": str(error), "time": monotonic_now()}
                )
            self._emit_locked(status, error=error)

    def _emit_locked(self, status: str, error: Optional[str] = None):
        turn = deepcopy(self.current_turn)
        self.current_turn = None

        timestamps = turn["timestamps"]
        payload = {
            "event": "turn_perf",
            "turn_id": turn["turn_id"],
            "session_id": turn["session_id"],
            "sentence_id": turn["sentence_id"],
            "status": status,
            "source": turn["source"],
            "conn_from": turn["conn_from"],
            "selected_module": turn["selected_module"],
            "providers": turn["providers"],
            "started_at": turn["started_at_wall"],
            "query_length": turn["query_length"],
            "query_preview": turn["query_preview"],
            "depth_max": turn["depth_max"],
            "llm_call_count": turn["llm_call_count"],
            "llm_chunk_count": turn["llm_chunk_count"],
            "llm_chars": turn["llm_chars"],
            "has_tool_call": turn["has_tool_call"],
            "tool_call_count": turn["tool_call_count"],
            "tool_batch_count": turn["tool_batch_count"],
            "tool_calls": turn["tool_calls"],
            "durations_ms": {
                "asr_ms": _duration_ms(
                    timestamps["asr_started_at"], timestamps["asr_finished_at"]
                ),
                "asr_to_stt_ms": _duration_ms(
                    timestamps["asr_finished_at"], timestamps["stt_sent_at"]
                ),
                "pre_llm_ms": _duration_ms(
                    timestamps["llm_prepare_started_at"], timestamps["llm_started_at"]
                ),
                "llm_first_chunk_ms": _duration_ms(
                    timestamps["llm_started_at"], timestamps["llm_first_chunk_at"]
                ),
                "llm_ttft_ms": _duration_ms(
                    timestamps["llm_started_at"], timestamps["llm_first_text_at"]
                ),
                "llm_total_ms": _duration_ms(
                    timestamps["llm_started_at"], timestamps["llm_finished_at"]
                )
                if not turn["llm_durations_ms"]
                else round(sum(turn["llm_durations_ms"]), 3),
                "tool_total_ms": _duration_ms(
                    timestamps["tool_batch_started_at"],
                    timestamps["tool_batch_finished_at"],
                )
                if not turn["tool_batch_durations_ms"]
                else round(sum(turn["tool_batch_durations_ms"]), 3),
                "tts_prepare_ms": _duration_ms(
                    timestamps["tts_text_queued_at"], timestamps["tts_started_at"]
                ),
                "tts_first_packet_ms": _duration_ms(
                    timestamps["tts_text_queued_at"], timestamps["tts_first_packet_at"]
                ),
                "tts_total_ms": _duration_ms(
                    timestamps["tts_text_queued_at"], timestamps["tts_finished_at"]
                ),
                "speech_to_first_packet_ms": _duration_ms(
                    timestamps["asr_started_at"], timestamps["tts_first_packet_at"]
                ),
                "turn_e2e_ms": _duration_ms(
                    timestamps["asr_started_at"], timestamps["tts_finished_at"]
                ),
            },
            "error": error,
            "errors": [
                {"stage": item["stage"], "error": item["error"]}
                for item in turn["errors"]
            ],
        }
        self.perf_logger.info(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _build_preview(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        compact_text = " ".join(text.split())
        return compact_text[:120]

    def _build_provider_info(self) -> Dict[str, Optional[str]]:
        selected_module = self.conn.config.get("selected_module", {})
        return {
            "vad": selected_module.get("VAD"),
            "asr": selected_module.get("ASR"),
            "llm": selected_module.get("LLM"),
            "tts": selected_module.get("TTS"),
            "memory": selected_module.get("Memory"),
            "intent": selected_module.get("Intent"),
        }
