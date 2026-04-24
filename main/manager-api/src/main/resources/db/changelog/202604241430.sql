-- 注册远程音频播放插件
INSERT INTO ai_model_provider (id, model_type, provider_code, name, fields,
                               sort, creator, create_date, updater, update_date)
VALUES ('SYSTEM_PLUGIN_PLAY_REMOTE_AUDIO',
        'Plugin',
        'play_remote_audio',
        '远程音频播放',
        JSON_ARRAY(),
        80, 0, NOW(), 0, NOW())
ON DUPLICATE KEY UPDATE
        model_type = VALUES(model_type),
        provider_code = VALUES(provider_code),
        name = VALUES(name),
        fields = VALUES(fields),
        sort = VALUES(sort),
        updater = VALUES(updater),
        update_date = VALUES(update_date);
