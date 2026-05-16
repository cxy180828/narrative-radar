# Narrative Radar v2

链上动量雷达 — 多链 Meme 币监控系统，AI 增强叙事识别，Telegram 实时推送。

## 核心功能

| 模块 | 能力 |
|------|------|
| **数据采集** | GMGN + DexScreener + pump.fun + Four.Meme，覆盖 ETH/BSC/BASE/SOL |
| **动量追踪** | 连涨检测 + 衰减识别 + 推送冷却，避免假突破和重复推送 |
| **叙事分类** | 关键词匹配 + AI 语义兜底（Groq/DeepSeek/Gemini 多供应商自动切换） |
| **安全检测** | GoPlus (EVM) + RugCheck (SOL)，蜜罐/税率/权限/代理合约全检 |
| **评分引擎** | 10+ 维度加权评分 (0-100)，分级推送 |
| **TG 交互** | Bot 命令：暂停/过滤/拉黑/加词/标记误报/手动报告 |
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
# 必填 — Telegram 推送
TELEGRAM_BOT_TOKEN=你的Bot Token
TG_CHAT_ID=你的聊天ID

# 选填 — AI 功能（至少填一个，推荐 Groq 免费）
GROQ_API_KEY=你的Groq密钥
DEEPSEEK_API_KEY=你的DeepSeek密钥
GEMINI_API_KEY=你的Gemini密钥
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
sudo cat > /etc/systemd/system/narrative-radar.service << 'EOF'
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
sudo systemctl status narrative-radar
```

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
| `/report` | 手动触发日报 |

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
- 75+ 分：立即推送 + AI 增强文案
- 50-74 分：汇总推送
- 50 以下：仅记录

## AI 功能

系统支持三个 AI 供应商，按优先级自动切换：

| 供应商 | 免费额度 | 特点 |
|--------|---------|------|
| **Groq** | 14,400 请求/天 | 最快，免费层完全够用 |
| **DeepSeek** | 500 万 Token 新用户赠送 | 最便宜 ($0.14/M) |
| **Gemini** | 1,000 请求/天 | Google 免费层 |

AI 使用场景：
- 关键词无法判断时的语义分类
- 代币描述质量评估
- 高分信号文案增强
- 每 6 小时热词自动发现
- 每日市场总结
- 误报特征分析

> 不配置任何 AI Key 也能正常运行，只是降级为纯规则匹配模式。

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
│   ├── client.py        #   多供应商 LLM 客户端 (自动切换+限流)
│   ├── narrative.py     #   叙事语义分析
│   ├── hotwords.py      #   热词自动发现
│   ├── summary.py       #   每日总结 + 文案增强
│   └── learning.py      #   误报学习 + 评分校准
│
├── engine/              # 核心引擎
│   ├── classifier.py    #   叙事分类器 (规则+AI)
│   ├── momentum.py      #   动量追踪器
│   ├── scorer.py        #   多维评分引擎
│   ├── safety.py        #   安全检测 (GoPlus+RugCheck)
│   └── backtest.py      #   推送后价格追踪
│
├── fetcher/             # 数据采集
│   ├── gmgn.py          #   GMGN API (主力数据源)
│   ├── dexscreener.py   #   DexScreener (备用+描述)
│   ├── pumpfun.py       #   pump.fun (SOL 新币)
│   └── fourmeme.py      #   Four.Meme (BSC 新币)
│
├── notify/              # 推送交互
│   ├── telegram.py      #   TG 消息发送
│   ├── formatter.py     #   消息格式化
│   └── bot_commands.py  #   Bot 命令处理
│
├── storage/             # 存储层
│   ├── database.py      #   SQLite (WAL模式)
│   └── cache.py         #   内存缓存 (TTL)
│
└── infra/               # 基础设施
    ├── http_client.py   #   HTTP (Session复用+重试+UA轮换)
    ├── logger.py        #   结构化日志 (轮转)
    ├── signals.py       #   优雅退出
    └── health.py        #   启动自检+磁盘监控
```

## 配置说明

所有参数都在 `config.yaml` 中，主要可调项：

```yaml
scan:
  interval: 30            # 扫描间隔（秒）
  chains: ["eth", "bsc", "base", "sol"]  # 监控的链

thresholds:
  min_market_cap: 1000    # 最低市值 ($)
  max_market_cap: 10000000 # 最高市值 ($)
  min_liquidity: 500      # 最低流动性 ($)
  max_sell_tax: 0.10      # 最高卖出税 (10%)
  min_age_minutes: 10     # 最短币龄（分钟）

momentum:
  consecutive_up: 3       # 连涨多少轮触发
  min_pct_gain: 5.0       # 最低涨幅 (%)
  push_cooldown: 300      # 同币推送冷却（秒）

push:
  high_score_threshold: 75  # 立即推送阈值
  medium_score_threshold: 50 # 汇总推送阈值
```

## 月成本

| 组件 | 费用 |
|------|------|
| Groq AI (免费层) | $0 |
| DeepSeek (如果 Groq 超额) | $2-5/月 |
| 链上数据 API | $0 (全免费) |
| Telegram Bot | $0 |
| **总计** | **$0 ~ $5/月** |

## License

MIT
