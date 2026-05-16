# Narrative Radar v2

链上动量雷达 — 多链 Meme 币监控系统，AI 增强叙事识别，Telegram 实时推送。

## 核心功能

| 模块 | 能力 |
|------|------|
| **数据采集** | GMGN + DexScreener + pump.fun + Four.Meme，覆盖 ETH/BSC/BASE/SOL |
| **动量追踪** | 连涨检测 + 衰减识别 + 推送冷却，避免假突破和重复推送 |
| **叙事分类** | 关键词匹配 + AI 语义兜底（支持中转站/官方API/本地模型，多供应商自动 fallback） |
| **安全检测** | GoPlus (EVM) + RugCheck (SOL)，蜜罐/税率/权限/代理合约全检 |
| **评分引擎** | 10+ 维度加权评分 (0-100)，分级推送 |
| **TG 交互** | Bot 命令：暂停/过滤/拉黑/加词/标记误报/AI状态/手动报告 |
| **推送渠道** | Telegram + 飞书，双渠道可同时启用 |
| **自学习** | 推送后追踪胜率、AI 热词发现、误报分析、评分自动校准 |

## 快速开始

### 1. 环境要求

- Python 3.8+
- 依赖：`requests`, `PyYAML`

### 2. 安装

```bash
git clone https://github.com/cxy180828/narrative-radar.git
cd narrative-radar
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# 必填 — 推送渠道（Telegram 和飞书至少配一个）
TELEGRAM_BOT_TOKEN=你的Bot Token
TG_CHAT_ID=你的聊天ID

# 飞书推送（可选，可与 Telegram 同时使用）
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 选填 — AI 功能（至少填一个，推荐 Groq 免费）
GROQ_API_KEY=你的Groq密钥
DEEPSEEK_API_KEY=你的DeepSeek密钥
GEMINI_API_KEY=你的Gemini密钥

# 中转站（如果有的话，放第一优先级）
RELAY_API_KEY=你的中转站密钥
```

### 4. 运行

```bash
# 直接运行
python3 main.py

# 后台运行 (screen)
screen -S radar
python3 main.py
# Ctrl+A D 脱离

# Docker 运行（推荐）
docker compose up -d
```

### 5. Systemd 服务（推荐生产环境）

```bash
sudo tee /etc/systemd/system/narrative-radar.service << 'EOF'
[Unit]
Description=Narrative Radar v2
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/path/to/narrative-radar
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
EnvironmentFile=/path/to/narrative-radar/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable narrative-radar
sudo systemctl start narrative-radar
```

---

## AI 供应商配置

系统支持**任何 OpenAI 兼容 API**，包括中转站、官方接口、聚合器、本地模型。按优先级顺序尝试，失败自动切换下一个。

### 配置方式

在 `config.yaml` 的 `ai.providers` 列表中添加供应商，**排在前面的优先级更高**：

```yaml
ai:
  enabled: true
  providers:
    # 中转站（最高优先级）
    - name: my-relay
      base_url: "https://your-relay.com/v1"
      model: "gpt-4o-mini"
      api_key_env: "RELAY_API_KEY"    # 从环境变量读取
      # api_key: "sk-xxx"            # 或直接写在这里
      max_rpm: 60
      max_rpd: 10000
      priority: 0
      timeout: 30
      tags: ["smart", "fast"]

    # 免费 fallback
    - name: groq
      base_url: "https://api.groq.com/openai/v1"
      model: "llama-3.3-70b-versatile"
      api_key_env: "GROQ_API_KEY"
      max_rpm: 28
      max_rpd: 14000
      priority: 1
      tags: ["fast", "free"]
```

### 每个 Provider 可配字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 显示名称（日志/状态查看） |
| `base_url` | 是 | API 端点，必须兼容 OpenAI `/chat/completions` |
| `model` | 是 | 请求的模型名 |
| `api_key_env` | 否 | API Key 的环境变量名 |
| `api_key` | 否 | 直接填写 API Key（与 api_key_env 二选一） |
| `max_rpm` | 否 | 每分钟最大请求数（默认 60） |
| `max_rpd` | 否 | 每天最大请求数（默认 100000） |
| `priority` | 否 | 优先级，越小越先尝试（默认按配置顺序） |
| `timeout` | 否 | 请求超时秒数（默认 30） |
| `tags` | 否 | 标签列表，用于任务路由 |
| `extra_headers` | 否 | 额外 HTTP 头（OpenRouter 需要 HTTP-Referer） |
| `supports_json_mode` | 否 | 是否支持 response_format json_object（默认 true） |

### 预置供应商示例

| 供应商 | base_url | 费用 | 特点 |
|--------|----------|------|------|
| **你的中转站** | `https://your-relay.com/v1` | 自定 | 主力，优先级最高 |
| **Groq** | `https://api.groq.com/openai/v1` | 免费 14400/天 | 极快 |
| **DeepSeek** | `https://api.deepseek.com/v1` | $0.14/M | 便宜又聪明 |
| **Gemini** | `https://generativelanguage.googleapis.com/v1beta/openai` | 免费 1000/天 | Google 免费层 |
| **OpenRouter** | `https://openrouter.ai/api/v1` | 按模型 | 100+ 模型聚合 |
| **SiliconFlow** | `https://api.siliconflow.cn/v1` | 低价 | 国内快速 |
| **OpenAI** | `https://api.openai.com/v1` | 按量 | GPT-4o-mini |
| **Together AI** | `https://api.together.xyz/v1` | 低价 | 高速推理 |
| **Fireworks** | `https://api.fireworks.ai/inference/v1` | 低价 | 低延迟 |
| **Ollama** | `http://localhost:11434/v1` | 免费 | 本地部署 |

### Fallback 机制

```
请求 → Provider 1 (中转站) → 成功 → 返回结果
              ↓ 失败/限流/超时
         Provider 2 (Groq) → 成功 → 返回结果
              ↓ 失败
         Provider 3 (DeepSeek) → 成功 → 返回结果
              ↓ 全部失败
         返回 None（降级到纯规则模式）
```

- **限流退避**：连续失败 3 次后冷却 30-300 秒
- **指数退避**：5+ 次失败后按指数增长冷却（最长 600 秒）
- **认证失败**：401/403 直接标记长期不可用
- **延迟追踪**：记录每个 provider 的平均响应速度
- **成功率统计**：通过 `/ai_status` 命令实时查看

---

## Telegram Bot 命令

| 命令 | 说明 |
|------|------|
| `/status` | 查看运行状态 |
| `/pause` | 暂停推送 |
| `/resume` | 恢复推送 |
| `/filter sol` | 只看 SOL 链信号 |
| `/unfilter` | 取消链过滤 |
| `/blacklist <地址>` | 拉黑某个代币 |
| `/addkw <分类> <关键词>` | 动态添加关键词 |
| `/fp <地址>` | 标记为误报 |
| `/winrate` | 查看 7 天胜率 |
| `/ai_status` | 查看 AI 供应商状态 |
| `/report` | 手动触发日报 |

---

## 飞书推送

支持飞书自定义机器人 webhook 推送，与 Telegram 可同时使用。

### 配置步骤

1. 在飞书群中点击 **设置 → 群机器人 → 添加机器人 → 自定义机器人**
2. 复制生成的 **Webhook URL**
3. （可选）开启签名校验并记录密钥

在 `.env` 中配置：

```env
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/your-hook-id
FEISHU_WEBHOOK_SECRET=your_secret  # 可选，开启签名校验时填写
```

### 推送格式

| 消息类型 | 场景 |
|---------|------|
| **卡片消息** (Interactive Card) | 信号推送 — 包含评分、链、叙事、快速链接按钮 |
| **纯文本** | 启动/关闭通知、每日报告、热词发现 |

### 双渠道行为

- Telegram 和飞书**独立工作**，任意一个配置即可
- 两个都配 → 信号同时推送到两个渠道
- Telegram Bot 命令（/pause 等）只影响 Telegram 推送，飞书不受影响
- 只配飞书不配 Telegram 也能正常工作（但没有交互命令功能）

---

## 信号评分维度

| 维度 | 最高分 | 说明 |
|------|--------|------|
| 涨幅幅度 | 35 | 连涨窗口内的总涨幅 |
| 连涨轮数 | 25 | 超过 3 轮后每轮 +5 |
| 重复触发 | 15 | 同一币多次创新高 |
| 买单维持 | 10 | 买入量未明显下降 |
| 1h 涨幅 | 8 | 短期趋势确认 |
| 聪明钱 | 5 | Smart money 进入 |
| 流动性/市值比 | 4 | 流动性质量 |
| 币龄 | 3 | 新币加分 |
| 叙事分类 | 3 | 热门叙事加分 |
| AI 叙事 | 3 | AI 识别有效叙事 |
| 描述质量 | 3 | 社交渠道齐全 |
| 社交链接 | 2 | 有推特/TG/官网 |

**推送规则**：
- **75+ 分**：立即推送 + AI 增强文案
- **50-74 分**：汇总推送
- **50 以下**：仅记录不推送

---

## 项目结构

```
narrative-radar/
├── main.py              # 入口 + 主循环调度
├── config.yaml          # 所有可调参数
├── requirements.txt     # Python 依赖
├── Dockerfile           # 容器镜像
├── docker-compose.yml   # 一键部署
├── .env.example         # 环境变量模板
│
├── ai/                  # AI 模块
│   ├── client.py        #   多供应商 LLM 客户端 (fallback+限流+延迟追踪)
│   ├── narrative.py     #   叙事语义分析
│   ├── hotwords.py      #   热词自动发现
│   ├── summary.py       #   每日总结 + 文案增强
│   └── learning.py      #   误报学习 + 评分校准
│
├── engine/              # 核心引擎
│   ├── classifier.py    #   叙事分类器 (规则+动态热词+AI)
│   ├── momentum.py      #   动量追踪器 (连涨+衰减)
│   ├── scorer.py        #   多维评分引擎
│   ├── safety.py        #   安全检测 (GoPlus+RugCheck)
│   └── backtest.py      #   推送后价格追踪
│
├── fetcher/             # 数据采集
│   ├── gmgn.py          #   GMGN API (主力数据源, 4链)
│   ├── dexscreener.py   #   DexScreener (备用+描述获取)
│   ├── pumpfun.py       #   pump.fun (SOL 新币)
│   └── fourmeme.py      #   Four.Meme (BSC 新币)
│
├── notify/              # 推送交互
│   ├── telegram.py      #   TG 消息发送 (Markdown+fallback)
│   ├── feishu.py        #   飞书 webhook (卡片+富文本+签名)
│   ├── formatter.py     #   消息格式化 (含快速链接)
│   └── bot_commands.py  #   Bot 命令处理 (12个命令)
│
├── storage/             # 存储层
│   ├── database.py      #   SQLite (WAL模式, 8张表)
│   └── cache.py         #   内存缓存 (TTL+LRU)
│
└── infra/               # 基础设施
    ├── http_client.py   #   HTTP (Session复用+重试+UA轮换)
    ├── logger.py        #   结构化日志 (JSON+轮转)
    ├── signals.py       #   优雅退出 (SIGINT/SIGTERM)
    └── health.py        #   启动自检+磁盘监控
```

---

## 常用配置调优

```yaml
# 扫描频率（越短越快发现，但越容易被限流）
scan:
  interval: 30            # 正常 30s，积极可调到 20s

# 触发灵敏度
momentum:
  consecutive_up: 3       # 降到 2 = 更灵敏但更多噪音
  min_pct_gain: 5.0       # 降到 3.0 = 更多信号

# 推送门槛
push:
  high_score_threshold: 75  # 降到 65 = 更多即时推送
  medium_score_threshold: 50 # 降到 40 = 更多汇总推送

# 安全过滤
thresholds:
  min_market_cap: 1000    # 提高到 5000 = 过滤更多垃圾
  max_sell_tax: 0.10      # 降到 0.05 = 更严格
```

---

## 月成本估算

| 方案 | AI 费用 | 总计 |
|------|---------|------|
| 纯免费 (Groq + Gemini) | $0 | $0 |
| 低成本 (Groq 主力 + DeepSeek 备用) | $2-5 | $2-5 |
| 中转站 + 免费 fallback | 取决于中转站价格 | $5-20 |
| 无 AI（纯规则模式） | $0 | $0 |

> 不配置任何 AI Key 也能正常运行，只是降级为纯关键词匹配模式，不影响动量追踪和推送。

---

## License

MIT
