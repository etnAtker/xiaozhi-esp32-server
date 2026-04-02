import json
import os
from collections import deque
from typing import Dict, List, Optional

from aiohttp import web


DEFAULT_TURN_LIMIT = 20
MAX_TURN_LIMIT = 200


class PerfLogReader:
    def __init__(self, config: dict):
        log_dir = config.get("log", {}).get("log_dir", "tmp")
        self.perf_log_path = os.path.join(log_dir, "perf.log")

    def get_recent_turns(self, limit: int = DEFAULT_TURN_LIMIT) -> List[Dict]:
        limit = max(1, min(limit, MAX_TURN_LIMIT))
        if not os.path.exists(self.perf_log_path):
            return []

        recent_lines = deque(maxlen=limit)
        with open(self.perf_log_path, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                line = line.strip()
                if line:
                    recent_lines.append(line)

        turns = []
        for line in reversed(recent_lines):
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return turns

    def get_turn_by_id(self, turn_id: str) -> Optional[Dict]:
        if not turn_id or not os.path.exists(self.perf_log_path):
            return None

        with open(self.perf_log_path, "r", encoding="utf-8", errors="ignore") as file:
            for line in file:
                if turn_id not in line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("turn_id") == turn_id:
                    return item
        return None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_stage_rows(turn: Dict) -> List[Dict]:
    durations = turn.get("durations_ms", {})
    tts_prepare_ms = durations.get("tts_prepare_ms") or 0
    tts_total_ms = durations.get("tts_total_ms") or 0
    tts_playback_ms = max(tts_total_ms - tts_prepare_ms, 0)

    rows = [
        {"key": "asr_ms", "label": "ASR", "value": durations.get("asr_ms") or 0},
        {
            "key": "pre_llm_ms",
            "label": "LLM 准备",
            "value": durations.get("pre_llm_ms") or 0,
        },
        {
            "key": "llm_ttft_ms",
            "label": "LLM 首字延迟",
            "value": durations.get("llm_ttft_ms") or 0,
        },
        {
            "key": "llm_total_ms",
            "label": "LLM 总耗时",
            "value": durations.get("llm_total_ms") or 0,
        },
        {
            "key": "tool_total_ms",
            "label": "工具总耗时",
            "value": durations.get("tool_total_ms") or 0,
        },
        {
            "key": "tts_prepare_ms",
            "label": "TTS 准备",
            "value": tts_prepare_ms,
        },
        {
            "key": "tts_playback_ms",
            "label": "TTS 播放",
            "value": tts_playback_ms,
        },
        {
            "key": "turn_e2e_ms",
            "label": "全链路",
            "value": durations.get("turn_e2e_ms") or 0,
        },
    ]
    max_value = max((row["value"] for row in rows), default=1) or 1
    for row in rows:
        row["width_pct"] = round(row["value"] / max_value * 100, 2)
        row["display"] = _format_ms(row["value"])
    return rows


def _format_ms(value) -> str:
    if value is None:
        return "-"
    try:
        value = float(value)
        if value < 100:
            return f"{value:.0f} ms"
        return f"{value / 1000:.2f} s"
    except (TypeError, ValueError):
        return "-"


def _format_turn_summary(turn: Dict) -> Dict:
    durations = turn.get("durations_ms", {})
    return {
        "turn_id": turn.get("turn_id"),
        "session_id": turn.get("session_id"),
        "status": turn.get("status"),
        "started_at": turn.get("started_at"),
        "query_preview": turn.get("query_preview") or "",
        "query_length": turn.get("query_length") or 0,
        "source": turn.get("source"),
        "conn_from": turn.get("conn_from"),
        "selected_module": turn.get("selected_module"),
        "providers": turn.get("providers", {}),
        "llm_call_count": turn.get("llm_call_count") or 0,
        "tool_call_count": turn.get("tool_call_count") or 0,
        "tool_batch_count": turn.get("tool_batch_count") or 0,
        "llm_chunk_count": turn.get("llm_chunk_count") or 0,
        "llm_chars": turn.get("llm_chars") or 0,
        "durations_ms": durations,
        "tool_calls": turn.get("tool_calls", []),
        "errors": turn.get("errors", []),
        "stage_rows": _build_stage_rows(turn),
    }


async def observability_page(request: web.Request) -> web.Response:
    reader = PerfLogReader(request.app["app_config"])
    turns = [_format_turn_summary(item) for item in reader.get_recent_turns()]
    initial_turn = turns[0] if turns else None
    turns_json = json.dumps(turns, ensure_ascii=False).replace("</", "<\\/")
    initial_turn_json = json.dumps(initial_turn, ensure_ascii=False).replace(
        "</", "<\\/"
    )

    html = OBSERVABILITY_HTML.replace(
        "__INITIAL_TURNS__",
        json.dumps(turns_json, ensure_ascii=False),
    ).replace(
        "__INITIAL_TURN__",
        json.dumps(initial_turn_json, ensure_ascii=False),
    )
    return web.Response(text=html, content_type="text/html")


async def observability_turns_api(request: web.Request) -> web.Response:
    limit = _safe_int(request.query.get("limit"), DEFAULT_TURN_LIMIT)
    reader = PerfLogReader(request.app["app_config"])
    turns = [_format_turn_summary(item) for item in reader.get_recent_turns(limit=limit)]
    return web.json_response({"items": turns, "limit": min(max(limit, 1), MAX_TURN_LIMIT)})


async def observability_turn_detail_api(request: web.Request) -> web.Response:
    turn_id = request.match_info.get("turn_id", "")
    reader = PerfLogReader(request.app["app_config"])
    turn = reader.get_turn_by_id(turn_id)
    if turn is None:
        return web.json_response({"error": "未找到记录"}, status=404)
    return web.json_response(_format_turn_summary(turn))


OBSERVABILITY_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>性能观测</title>
  <style>
    :root {
      --bg: #f3efe7;
      --panel: #fffaf0;
      --line: #d7c8af;
      --text: #1d1d1b;
      --muted: #6e675c;
      --accent: #b14d28;
      --accent-soft: #efd7bf;
      --ok: #2f7d4c;
      --warn: #9b5b00;
      --err: #a12622;
      --shadow: 0 10px 30px rgba(45, 31, 14, 0.08);
    }

    html, body {
      min-height: 100%;
      background-color: #efe8dc;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      background: #efe8dc;
    }

    .layout {
      max-width: 1560px;
      margin: 0 auto;
      padding: 24px;
    }

    .header {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 20px;
    }

    .title {
      font-size: 32px;
      font-weight: 800;
      letter-spacing: 0.02em;
      margin: 0;
    }

    .subtitle {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 14px;
    }

    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
    }

    .actions input {
      width: 320px;
      max-width: 48vw;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.85);
      border-radius: 12px;
      padding: 10px 14px;
      color: var(--text);
    }

    .actions button {
      border: 0;
      border-radius: 12px;
      padding: 10px 14px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }

    .panel {
      background: rgba(255, 250, 240, 0.95);
      border: 1px solid rgba(183, 160, 124, 0.45);
      border-radius: 22px;
      box-shadow: var(--shadow);
      overflow: hidden;
      backdrop-filter: blur(8px);
    }

    .panel-header {
      padding: 18px 20px 14px;
      border-bottom: 1px solid rgba(183, 160, 124, 0.35);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }

    .panel-title {
      margin: 0;
      font-size: 16px;
      font-weight: 800;
    }

    .panel-desc {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 12px;
    }

    .table-wrap {
      max-height: 74vh;
      overflow: auto;
    }

    .board {
      position: relative;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }

    th, td {
      padding: 12px 14px;
      border-bottom: 1px solid rgba(183, 160, 124, 0.22);
      text-align: left;
      vertical-align: top;
    }

    th {
      position: sticky;
      top: 0;
      background: #fbf4e8;
      z-index: 1;
      font-size: 12px;
      letter-spacing: 0.04em;
      color: var(--muted);
      text-transform: uppercase;
    }

    tbody tr {
      cursor: pointer;
      transition: background 140ms ease;
    }

    tbody tr:hover {
      background: rgba(238, 216, 187, 0.38);
    }

    tbody tr.active {
      background: rgba(239, 215, 191, 0.82);
    }

    .mono {
      font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
    }

    .truncate {
      max-width: 280px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: var(--accent-soft);
      color: var(--accent);
    }

    .badge.completed { background: #dcefdc; color: var(--ok); }
    .badge.running { background: #f6e7b8; color: var(--warn); }
    .badge.aborted, .badge.failed, .badge.closed { background: #f3d7d5; color: var(--err); }

    .empty {
      padding: 36px 24px;
      color: var(--muted);
    }

    .summary {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }

    .metric {
      border: 1px solid rgba(183, 160, 124, 0.3);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.65);
    }

    .metric-label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }

    .metric-value {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }

    .section {
      margin-top: 16px;
      border: 1px solid rgba(183, 160, 124, 0.28);
      border-radius: 18px;
      padding: 16px;
      background: rgba(255,255,255,0.58);
    }

    .section h3 {
      margin: 0 0 12px;
      font-size: 14px;
    }

    .kv-grid {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 8px 12px;
      font-size: 13px;
    }

    .kv-key {
      color: var(--muted);
    }

    .providers {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .provider-pill {
      border-radius: 999px;
      padding: 6px 10px;
      background: #f3e6d2;
      font-size: 12px;
    }

    .bars {
      display: grid;
      gap: 10px;
    }

    .bar-row {
      display: grid;
      grid-template-columns: 120px minmax(0, 1fr) 84px;
      gap: 10px;
      align-items: center;
      font-size: 13px;
    }

    .bar-track {
      height: 14px;
      border-radius: 999px;
      background: rgba(197, 178, 146, 0.35);
      overflow: hidden;
    }

    .bar-fill {
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #d36f38 0%, #8d3217 100%);
    }

    .tip {
      color: var(--muted);
      font-size: 12px;
      margin-top: 10px;
    }

    .drawer-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(34, 25, 17, 0.16);
      opacity: 0;
      pointer-events: none;
      transition: opacity 180ms ease;
      z-index: 20;
    }

    .drawer-backdrop.open {
      opacity: 1;
      pointer-events: auto;
    }

    .drawer {
      position: fixed;
      top: 18px;
      right: 18px;
      bottom: 18px;
      width: min(560px, calc(100vw - 32px));
      background: rgba(255, 250, 240, 0.98);
      border: 1px solid rgba(183, 160, 124, 0.45);
      border-radius: 24px;
      box-shadow: 0 18px 48px rgba(45, 31, 14, 0.18);
      backdrop-filter: blur(10px);
      transform: translateX(calc(100% + 24px));
      transition: transform 220ms ease;
      z-index: 30;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .drawer.open {
      transform: translateX(0);
    }

    .drawer-header {
      padding: 18px 20px 14px;
      border-bottom: 1px solid rgba(183, 160, 124, 0.35);
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }

    .drawer-title {
      margin: 0;
      font-size: 16px;
      font-weight: 800;
    }

    .drawer-desc {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 12px;
    }

    .drawer-close {
      border: 0;
      background: rgba(177, 77, 40, 0.12);
      color: var(--accent);
      width: 36px;
      height: 36px;
      border-radius: 12px;
      font-size: 20px;
      line-height: 1;
      cursor: pointer;
      flex: none;
    }

    .drawer-body {
      padding: 18px 20px 20px;
      overflow: auto;
    }

    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "Cascadia Code", "SFMono-Regular", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
    }

    @media (max-width: 1180px) {
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 720px) {
      .layout { padding: 14px; }
      .header { flex-direction: column; align-items: stretch; }
      .actions { flex-direction: column; }
      .actions input { width: 100%; max-width: none; }
      .summary { grid-template-columns: 1fr; }
      .bar-row { grid-template-columns: 1fr; }
      .drawer {
        top: 10px;
        right: 10px;
        left: 10px;
        bottom: 10px;
        width: auto;
      }
    }
  </style>
</head>
<body>
  <div class="layout">
    <div class="header">
      <div>
        <h1 class="title">性能观测</h1>
        <p class="subtitle">基于 <span class="mono">perf.log</span> 的对话链路性能观测页面，默认展示最近 20 条记录。</p>
      </div>
      <div class="actions">
        <input id="turnSearch" type="text" placeholder="输入 turn_id 精确查询" />
        <button id="refreshBtn" type="button">刷新最近 20 条</button>
      </div>
    </div>
    <div class="board">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2 class="panel-title">最近记录</h2>
            <p class="panel-desc">列表数据直接读取当前 <span class="mono">perf.log</span>。点击某一行后，右侧滑出详情抽屉。</p>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>状态</th>
                <th>用户输入</th>
                <th>全链路</th>
                <th>LLM</th>
                <th>TTS</th>
              </tr>
            </thead>
            <tbody id="turnTableBody"></tbody>
          </table>
        </div>
      </section>
    </div>
  </div>
  <div id="drawerBackdrop" class="drawer-backdrop"></div>
  <aside id="detailDrawer" class="drawer" aria-hidden="true">
    <div class="drawer-header">
      <div>
        <h2 class="drawer-title">记录详情</h2>
        <p class="drawer-desc">阶段耗时图为聚合耗时的可视化，不表示精确的时序重叠关系。</p>
      </div>
      <button id="drawerCloseBtn" class="drawer-close" type="button" aria-label="关闭详情">×</button>
    </div>
    <div id="detailRoot" class="drawer-body"></div>
  </aside>
  <script>
    const INITIAL_TURNS = JSON.parse(__INITIAL_TURNS__);
    const INITIAL_TURN = JSON.parse(__INITIAL_TURN__);
    const STATUS_TEXT = {
      completed: "完成",
      running: "运行中",
      aborted: "已中断",
      closed: "已关闭",
      superseded: "被覆盖",
      empty_asr: "空识别",
      asr_failed: "ASR失败",
      llm_init_failed: "LLM初始化失败",
    };

    const SOURCE_TEXT = {
      asr: "语音",
      text: "文本",
      unknown: "未知",
    };

    const CONN_FROM_TEXT = {
      ws: "WebSocket",
      mqtt_gateway: "MQTT网关",
    };

    const PROVIDER_LABELS = {
      vad: "VAD",
      asr: "ASR",
      llm: "LLM",
      tts: "TTS",
      memory: "记忆",
      intent: "意图",
    };

    const state = {
      turns: Array.isArray(INITIAL_TURNS) ? INITIAL_TURNS : [],
      selectedTurn: null,
      drawerOpen: false,
    };

    const bodyEl = document.getElementById("turnTableBody");
    const detailEl = document.getElementById("detailRoot");
    const searchEl = document.getElementById("turnSearch");
    const refreshBtn = document.getElementById("refreshBtn");
    const drawerEl = document.getElementById("detailDrawer");
    const drawerBackdropEl = document.getElementById("drawerBackdrop");
    const drawerCloseBtn = document.getElementById("drawerCloseBtn");

    function fmtMs(value) {
      if (value === null || value === undefined) return "-";
      const num = Number(value) || 0;
      if (num < 100) return `${Math.round(num)} ms`;
      return `${(num / 1000).toFixed(2)} s`;
    }

    function escapeHtml(text) {
      return String(text ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    async function fetchRecentTurns() {
      const res = await fetch("/ob/api/turns?limit=20");
      const data = await res.json();
      state.turns = data.items || [];
      if (state.turns.length > 0 && state.selectedTurn?.turn_id) {
        const selectedId = state.selectedTurn.turn_id;
        state.selectedTurn =
          state.turns.find(item => item.turn_id === selectedId) || state.selectedTurn;
        if (state.drawerOpen && state.selectedTurn?.turn_id) {
          await fetchTurnDetail(state.selectedTurn.turn_id, false);
        }
      } else {
        state.selectedTurn = null;
        state.drawerOpen = false;
      }
      renderTable();
      renderDetail();
    }

    async function fetchTurnDetail(turnId, rerender = true) {
      if (!turnId) return;
      const res = await fetch(`/ob/api/turns/${encodeURIComponent(turnId)}`);
      if (!res.ok) {
        state.drawerOpen = true;
        syncDrawer();
        detailEl.innerHTML = `<div class="empty">未找到记录：<span class="mono">${escapeHtml(turnId)}</span></div>`;
        return;
      }
      state.selectedTurn = await res.json();
      state.drawerOpen = true;
      if (rerender) {
        renderTable();
        renderDetail();
      }
    }

    function closeDrawer() {
      state.drawerOpen = false;
      renderTable();
      renderDetail();
    }

    function syncDrawer() {
      const opened = !!state.drawerOpen;
      drawerEl.classList.toggle("open", opened);
      drawerBackdropEl.classList.toggle("open", opened);
      drawerEl.setAttribute("aria-hidden", opened ? "false" : "true");
    }

    function renderTable() {
      if (!state.turns.length) {
        bodyEl.innerHTML = `<tr><td colspan="6" class="empty">当前 perf.log 中没有可展示的记录。</td></tr>`;
        return;
      }

      bodyEl.innerHTML = state.turns.map((item) => {
        const active =
          state.drawerOpen && item.turn_id === state.selectedTurn?.turn_id
            ? "active"
            : "";
        return `
          <tr class="${active}" data-turn-id="${escapeHtml(item.turn_id)}">
            <td class="mono">${escapeHtml(item.started_at || "-")}</td>
            <td><span class="badge ${escapeHtml(item.status || "")}">${escapeHtml(STATUS_TEXT[item.status] || item.status || "-")}</span></td>
            <td class="truncate" title="${escapeHtml(item.query_preview || "")}">${escapeHtml(item.query_preview || "-")}</td>
            <td class="mono">${fmtMs(item.durations_ms?.turn_e2e_ms)}</td>
            <td class="mono">${fmtMs(item.durations_ms?.llm_total_ms)}</td>
            <td class="mono">${fmtMs(item.durations_ms?.tts_total_ms)}</td>
          </tr>
        `;
      }).join("");

      for (const row of bodyEl.querySelectorAll("tr[data-turn-id]")) {
        row.addEventListener("click", async () => {
          await fetchTurnDetail(row.dataset.turnId);
        });
      }
    }

    function renderDetail() {
      const item = state.selectedTurn;
      if (!item || !state.drawerOpen) {
        detailEl.innerHTML = `<div class="empty">选择一条记录查看详情。</div>`;
        syncDrawer();
        return;
      }

      const providers = Object.entries(item.providers || {})
        .map(([key, value]) => `<span class="provider-pill">${escapeHtml(PROVIDER_LABELS[key] || key)}: ${escapeHtml(value || "-")}</span>`)
        .join("");

      const stageRows = (item.stage_rows || []).map((row) => `
        <div class="bar-row">
          <div>${escapeHtml(row.label)}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${row.width_pct}%"></div></div>
          <div class="mono">${escapeHtml(row.display)}</div>
        </div>
      `).join("");

      const toolCalls = item.tool_calls?.length
        ? `<pre>${escapeHtml(JSON.stringify(item.tool_calls, null, 2))}</pre>`
        : `<div class="tip">本轮没有记录到工具调用明细。</div>`;

      const errors = item.errors?.length
        ? `<pre>${escapeHtml(JSON.stringify(item.errors, null, 2))}</pre>`
        : `<div class="tip">本轮没有记录错误。</div>`;

      detailEl.innerHTML = `
        <div class="summary">
          <div class="metric"><div class="metric-label">全链路耗时</div><div class="metric-value">${fmtMs(item.durations_ms?.turn_e2e_ms)}</div></div>
          <div class="metric"><div class="metric-label">首包可听</div><div class="metric-value">${fmtMs(item.durations_ms?.speech_to_first_packet_ms)}</div></div>
          <div class="metric"><div class="metric-label">LLM 首字延迟</div><div class="metric-value">${fmtMs(item.durations_ms?.llm_ttft_ms)}</div></div>
          <div class="metric"><div class="metric-label">LLM 总耗时</div><div class="metric-value">${fmtMs(item.durations_ms?.llm_total_ms)}</div></div>
          <div class="metric"><div class="metric-label">TTS 首包</div><div class="metric-value">${fmtMs(item.durations_ms?.tts_first_packet_ms)}</div></div>
          <div class="metric"><div class="metric-label">TTS 总耗时</div><div class="metric-value">${fmtMs(item.durations_ms?.tts_total_ms)}</div></div>
        </div>

        <div class="section">
          <h3>基础信息</h3>
          <div class="kv-grid">
            <div class="kv-key">turn_id</div><div class="mono">${escapeHtml(item.turn_id || "-")}</div>
            <div class="kv-key">session_id</div><div class="mono">${escapeHtml(item.session_id || "-")}</div>
            <div class="kv-key">状态</div><div><span class="badge ${escapeHtml(item.status || "")}">${escapeHtml(STATUS_TEXT[item.status] || item.status || "-")}</span></div>
            <div class="kv-key">开始时间</div><div class="mono">${escapeHtml(item.started_at || "-")}</div>
            <div class="kv-key">用户输入</div><div>${escapeHtml(item.query_preview || "-")}</div>
            <div class="kv-key">来源</div><div>${escapeHtml(SOURCE_TEXT[item.source] || item.source || "-")} / ${escapeHtml(CONN_FROM_TEXT[item.conn_from] || item.conn_from || "-")}</div>
            <div class="kv-key">模块组合</div><div class="mono">${escapeHtml(item.selected_module || "-")}</div>
            <div class="kv-key">统计</div><div>LLM 调用: ${escapeHtml(item.llm_call_count)}，工具调用: ${escapeHtml(item.tool_call_count)}，工具批次: ${escapeHtml(item.tool_batch_count)}，Chunk 数: ${escapeHtml(item.llm_chunk_count)}，字符数: ${escapeHtml(item.llm_chars)}</div>
          </div>
        </div>

        <div class="section">
          <h3>模块信息</h3>
          <div class="providers">${providers || '<span class="tip">无 provider 信息</span>'}</div>
        </div>

        <div class="section">
          <h3>阶段耗时</h3>
          <div class="bars">${stageRows}</div>
          <div class="tip">说明：当前图表展示的是聚合耗时，适合快速判断哪一段占比高；由于流式回复与 TTS 可能重叠，不应将这些条形图视为精确 waterfall。</div>
        </div>

        <div class="section">
          <h3>工具调用</h3>
          ${toolCalls}
        </div>

        <div class="section">
          <h3>错误信息</h3>
          ${errors}
        </div>
      `;
      syncDrawer();
    }

    searchEl.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;
      const value = searchEl.value.trim();
      if (!value) return;
      await fetchTurnDetail(value);
    });

    refreshBtn.addEventListener("click", fetchRecentTurns);
    drawerBackdropEl.addEventListener("click", closeDrawer);
    drawerCloseBtn.addEventListener("click", closeDrawer);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.drawerOpen) {
        closeDrawer();
      }
    });

    renderTable();
    renderDetail();
  </script>
</body>
</html>"""
