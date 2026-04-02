-- 为 OpenAI LLM 增加请求参数覆盖字段
UPDATE `ai_model_provider`
SET `fields` = '[{"key":"base_url","label":"基础URL","type":"string"},{"key":"model_name","label":"模型名称","type":"string"},{"key":"api_key","label":"API密钥","type":"string"},{"key":"temperature","label":"温度","type":"number"},{"key":"max_tokens","label":"最大令牌数","type":"number"},{"key":"top_p","label":"top_p值","type":"number"},{"key":"top_k","label":"top_k值","type":"number"},{"key":"frequency_penalty","label":"频率惩罚","type":"number"},{"key":"request_overrides","label":"请求参数覆盖","type":"dict","dict_name":"request_overrides"}]'
WHERE `id` = 'SYSTEM_LLM_openai';
