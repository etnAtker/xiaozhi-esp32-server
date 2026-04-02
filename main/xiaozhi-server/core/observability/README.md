# observability

本目录用于承载 `xiaozhi-server` 当前对话链路的轻量性能观测能力。

当前实现目标不是做完整的 metrics / tracing 平台，而是先解决一个更直接的问题：

- 一轮语音对话到底慢在 `ASR`、`LLM`、`tool_call` 还是 `TTS`
- 出现高延迟时，是否能从日志里快速还原单轮链路
- 在不改现有配置、不增加额外 HTTP 接口的前提下，默认开启并低侵入运行

## 设计目标

当前设计遵循这几个原则：

- 默认开启，不依赖额外配置项
- 与业务日志分离，性能日志单独写入 `perf.log`
- 只在阶段边界埋点，不在高频音频帧上做重日志
- 一轮对话只输出一条 `turn_perf` JSON 日志，便于 grep / jq / ELK 分析
- 允许在 `server.log` 中保留少量初始化和异常信息，但性能明细尽量进入独立日志
- 提供只读观测页面，默认从 `perf.log` 读取最近 20 条 turn

## 日志位置

性能日志通过 `config.logger.create_perf_logger()` 写入 `log_dir/perf.log`。

默认配置下：

- `log_dir = tmp`
- `log_file = server.log`

因此如果进程从 `main/xiaozhi-server` 目录启动，通常会生成：

- `tmp/server.log`
- `tmp/perf.log`

注意：这里仍然依赖进程启动工作目录，因为 `log_dir` 是相对路径。

## 页面入口

当前已提供只读观测页面：

- `/ob`

以及只读数据接口：

- `/ob/api/turns`
- `/ob/api/turns/{turn_id}`

页面默认读取最近 20 条 turn，数据来源是当前 `perf.log`，不依赖内存缓存。

这意味着：

- 服务重启后，页面仍可查看当前 `perf.log` 中的历史 turn
- 页面与日志口径一致
- 当前页默认不读取轮转归档日志，只读取当前 `perf.log`

## 核心模型

当前核心实现位于：

- `performance.py`

其中的 `ConnectionPerformanceTracker` 负责：

- 为单个连接维护当前活跃 turn
- 记录阶段时间点
- 汇总工具调用与 LLM 调用统计
- 在 turn 结束时输出一条结构化 JSON

## Turn 生命周期

当前一轮 turn 的生命周期大致如下：

1. `ASR` 结束识别后，启动一个新的 turn
2. `startToChat()` 更新 query 文本
3. 发送 `stt` 消息给客户端
4. 进入 `ConnectionHandler.chat()`，开始 LLM 处理
5. 若识别到 `tool_call`，记录工具批次与单工具耗时
6. 文本进入 TTS 队列
7. 首个音频包发送到客户端
8. 发送 `tts stop` 后收口，输出一条 `turn_perf` 日志

如果过程中发生这些情况，也会提前结束并输出日志：

- `empty_asr`
- `asr_failed`
- `llm_init_failed`
- `aborted`
- `closed`
- `superseded`

## 埋点位置

当前埋点主要分布在以下模块：

- `core/providers/asr/base.py`
  - 创建 turn
  - 记录 `asr_started_at` / `asr_finished_at`
- `core/handle/receiveAudioHandle.py`
  - 更新 query
- `core/handle/sendAudioHandle.py`
  - 记录 `stt_sent_at`
  - 记录 `tts_started_at`
  - 记录 `tts_first_packet_at`
  - 记录 `tts_finished_at`
- `core/connection.py`
  - 记录 LLM 准备、首 chunk、首文本、结束
  - 记录 tool batch 总耗时
- `core/providers/tools/unified_tool_handler.py`
  - 记录单个 tool 的耗时与执行结果
- `core/providers/tts/base.py`
  - 记录文本进入 TTS 队列的时间
- `core/handle/abortHandle.py`
  - 在用户打断时收口 turn

## 事件时间点

当前 `timestamps` 字段包含如下时间点：

- `asr_started_at`
  - ASR 开始处理当前音频片段
- `asr_finished_at`
  - ASR 完成识别并得到文本
- `stt_sent_at`
  - 服务端向客户端发送 `stt` 消息完成
- `llm_prepare_started_at`
  - 进入 `chat()` 后开始准备 LLM 请求，包括记忆查询前的起点
- `llm_started_at`
  - 真正开始构造并发起 LLM 流式请求
- `llm_first_chunk_at`
  - 收到首个流式 chunk
- `llm_first_text_at`
  - 收到首个非空文本内容
- `llm_finished_at`
  - 本次 LLM 调用完成
- `tool_detected_at`
  - 首次识别到工具调用
- `tool_batch_started_at`
  - 当前一批工具开始执行
- `tool_batch_finished_at`
  - 当前一批工具执行完毕
- `tts_text_queued_at`
  - 文本首次进入 TTS 队列
- `tts_started_at`
  - TTS 音频发送流程开始
- `tts_first_packet_at`
  - 首个音频包真正发出
- `tts_finished_at`
  - `tts stop` 发送完成，整轮语音输出结束

## 输出字段说明

每一条性能日志都是一行 JSON，事件名固定为：

- `event = "turn_perf"`

### 顶层字段

- `turn_id`
  - 当前 turn 的唯一 ID
- `session_id`
  - 当前连接的会话 ID
- `sentence_id`
  - 当前语音输出使用的句子 ID
- `status`
  - 当前 turn 的收口状态
- `source`
  - turn 来源，当前主要是 `asr` 或 `text`
- `conn_from`
  - 连接来源，当前是 `ws` 或 `mqtt_gateway`
- `selected_module`
  - 当前连接使用的模块组合缩写
- `providers`
  - 当前 turn 使用的模块名称，包含 `vad/asr/llm/tts/memory/intent`
- `started_at`
  - turn 启动时的墙上时间，ISO 格式
- `query_length`
  - 用户输入文本长度
- `query_preview`
  - 用户输入预览，最多保留前 120 个字符
- `depth_max`
  - 当前 turn 中 `chat()` 递归深度最大值
- `llm_call_count`
  - 本轮中 LLM 被调用的次数
- `llm_chunk_count`
  - 本轮累计收到的 LLM 非空文本 chunk 数
- `llm_chars`
  - 本轮 LLM 输出文本字符数
- `has_tool_call`
  - 本轮是否发生过工具调用
- `tool_call_count`
  - 本轮记录到的工具调用次数
- `tool_batch_count`
  - 本轮工具批次次数
- `tool_calls`
  - 单个工具调用明细列表
- `error`
  - finalize 时附带的主错误信息
- `errors`
  - 本轮累计错误列表

### `providers` 字段

示例：

```json
{
  "vad": "VAD_silero",
  "asr": "ASR_fun_local",
  "llm": "LLM_openai",
  "tts": "TTS_edge",
  "memory": "Memory_mem_local_short",
  "intent": "Intent_function_call"
}
```

用于回答“当前日志是在哪套模型组合下产生的”。

### `tool_calls` 字段

`tool_calls` 是数组，每一项代表一次具体工具执行。

字段包括：

- `name`
  - 工具名
- `duration_ms`
  - 单工具执行耗时
- `action`
  - 工具执行返回的动作类型，例如 `RESPONSE`、`REQLLM`
- `success`
  - 是否成功
- `error`
  - 如果失败，记录错误信息

## durations_ms 字段说明

`durations_ms` 是最重要的分析区块。

- `asr_ms`
  - 从 `asr_started_at` 到 `asr_finished_at`
  - 表示语音识别总耗时

- `asr_to_stt_ms`
  - 从 `asr_finished_at` 到 `stt_sent_at`
  - 表示识别完成后到客户端看到 STT 的时间

- `pre_llm_ms`
  - 从 `llm_prepare_started_at` 到 `llm_started_at`
  - 表示 LLM 正式开始前的准备耗时，主要覆盖记忆查询等

- `llm_first_chunk_ms`
  - 从 `llm_started_at` 到 `llm_first_chunk_at`
  - 表示拿到首个流式 chunk 的时间

- `llm_ttft_ms`
  - 从 `llm_started_at` 到 `llm_first_text_at`
  - 表示拿到首个有效文本 token 的时间
  - 这是判断“LLM 首响应慢不慢”的核心指标

- `llm_total_ms`
  - 当前 turn 内所有 LLM 调用时长之和
  - 如果发生 `tool_call -> 回填 tool -> 再次请求 LLM`，这里会累计多次 LLM 调用

- `tool_total_ms`
  - 当前 turn 内所有工具批次总耗时
  - 不是单个工具，而是批次耗时累计

- `tts_prepare_ms`
  - 从 `tts_text_queued_at` 到 `tts_started_at`
  - 表示文本进入 TTS 队列后到开始发送音频前的等待时间

- `tts_first_packet_ms`
  - 从 `tts_text_queued_at` 到 `tts_first_packet_at`
  - 表示文本进入 TTS 后，首个音频包发出的时间
  - 这是判断“用户多久能听到第一句话”的核心指标

- `tts_total_ms`
  - 从 `tts_text_queued_at` 到 `tts_finished_at`
  - 表示当前轮 TTS 从进入队列到整轮播报完成的总耗时

- `speech_to_first_packet_ms`
  - 从 `asr_started_at` 到 `tts_first_packet_at`
  - 表示一次完整语音问答从识别开始到首包播出的时间

- `turn_e2e_ms`
  - 从 `asr_started_at` 到 `tts_finished_at`
  - 表示当前语音 turn 的端到端总耗时

## 一条示例日志

下面是一条示意，不保证字段值与线上完全一致：

```json
{
  "event": "turn_perf",
  "turn_id": "dbe1d7f6d3d94efcae9b4f0bc3dce123",
  "session_id": "96f1c3f6-5ccf-4d1c-82f6-1f0c5f2b9f3e",
  "sentence_id": "a2b4c6d8e0f14a2fb3c4d5e6f7080912",
  "status": "completed",
  "source": "asr",
  "conn_from": "ws",
  "selected_module": "sifuopeed00fu00",
  "query_length": 12,
  "query_preview": "今天天气怎么样",
  "llm_call_count": 1,
  "tool_call_count": 1,
  "tool_batch_count": 1,
  "durations_ms": {
    "asr_ms": 482.133,
    "asr_to_stt_ms": 11.204,
    "pre_llm_ms": 5.511,
    "llm_first_chunk_ms": 621.447,
    "llm_ttft_ms": 648.902,
    "llm_total_ms": 1432.778,
    "tool_total_ms": 318.442,
    "tts_prepare_ms": 74.229,
    "tts_first_packet_ms": 211.884,
    "tts_total_ms": 1743.882,
    "speech_to_first_packet_ms": 2354.746,
    "turn_e2e_ms": 3669.907
  }
}
```

## 如何解读日志

几种常见定位方式：

- `llm_ttft_ms` 高
  - 说明首 token 慢，优先看 LLM 服务、网络、上下文大小

- `tool_total_ms` 高
  - 说明耗时主要在工具执行，不一定是模型慢

- `tts_prepare_ms` 高
  - 说明文本已产生，但 TTS 线程或队列排队慢

- `tts_first_packet_ms` 高，而 `tts_prepare_ms` 不高
  - 说明更可能是 TTS 合成本身慢

- `tts_total_ms` 高，但 `tts_first_packet_ms` 正常
  - 说明首包快，但整段播放/流控拖长

- `asr_ms` 高
  - 优先看识别模型、上传音频质量、流式/非流式 ASR 差异

## 当前限制

当前实现有这些边界：

- 还没有对外暴露 `/metrics` 或 `/debug` HTTP 接口
- 还没有接 Prometheus / OpenTelemetry
- 当前是连接级单活跃 turn 模型，不支持一个连接并行多轮对话
- `tool_total_ms`、`llm_total_ms` 是按 turn 聚合值，不区分更细粒度阶段树
- 某些系统播报或异常提前退出场景，可能没有完整的 `turn_e2e_ms`

## 后续可扩展方向

如果后续要继续扩展，建议顺序如下：

1. 增加最近 N 条 turn 的内存 ring buffer
2. 提供只读调试接口
3. 增加 Prometheus 指标
4. 将 `query_preview`、`tool_calls` 与错误码再做标准化
5. 针对流式 ASR 与流式 TTS 补更细的 provider 内部阶段耗时
