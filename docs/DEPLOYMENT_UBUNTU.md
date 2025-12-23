# Ubuntu 部署指南

## 🖥️ 系统要求

- **操作系统**: Ubuntu 20.04 / 22.04 / 24.04 LTS 或 Debian 11 / 12
- **内存**: 最低 512MB，推荐 1GB+
- **磁盘**: 最低 500MB 可用空间
- **网络**: 需要访问 `www.deribit.com` 或 `test.deribit.com`

---

## 🚀 快速安装（一键部署）

```bash
# 1. 克隆项目
git clone https://github.com/your-repo/deribit-mcp-server.git
cd deribit-mcp-server

# 2. 运行安装脚本（需要 sudo）
sudo bash scripts/install.sh

# 3. 编辑配置（设置 API 凭证）
sudo nano /etc/deribit-mcp/config.env

# 4. 重启服务使配置生效
sudo systemctl restart deribit-mcp

# 5. 验证
curl http://localhost:8000/health
```

---

## 📋 详细安装步骤

### 步骤 1: 准备系统

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装基础依赖
sudo apt install -y curl wget git build-essential
```

### 步骤 2: 克隆项目

```bash
cd /tmp
git clone https://github.com/your-repo/deribit-mcp-server.git
cd deribit-mcp-server
```

### 步骤 3: 运行安装脚本

```bash
sudo bash scripts/install.sh
```

安装脚本会自动完成以下操作：
- ✅ 安装 Python 3.10+（如果需要）
- ✅ 创建专用系统用户 `deribit`
- ✅ 安装应用到 `/opt/deribit-mcp`
- ✅ 创建 Python 虚拟环境
- ✅ 配置 systemd 服务（自动重启）
- ✅ 配置日志轮转

### 步骤 4: 配置

编辑配置文件：

```bash
sudo nano /etc/deribit-mcp/config.env
```

配置内容：

```bash
# =============================================================================
# Deribit MCP Server 配置
# =============================================================================

# 环境选择: prod 或 test
DERIBIT_ENV=prod

# Private API 开关 (true/false)
DERIBIT_ENABLE_PRIVATE=false

# API 凭证 (仅 Private API 需要)
# ⚠️ 请替换为真实凭证
DERIBIT_CLIENT_ID=your_client_id_here
DERIBIT_CLIENT_SECRET=your_client_secret_here

# 网络设置
DERIBIT_TIMEOUT_S=10
DERIBIT_MAX_RPS=8

# 缓存设置
DERIBIT_CACHE_TTL_FAST=1.0
DERIBIT_CACHE_TTL_SLOW=30.0

# 交易安全 (true = 只模拟，不执行)
DERIBIT_DRY_RUN=true

# HTTP 服务器设置
DERIBIT_HOST=0.0.0.0
DERIBIT_PORT=8000
```

重启服务使配置生效：

```bash
sudo systemctl restart deribit-mcp
```

---

## 🔧 systemd 服务管理

### 常用命令

```bash
# 查看服务状态
sudo systemctl status deribit-mcp

# 启动服务
sudo systemctl start deribit-mcp

# 停止服务
sudo systemctl stop deribit-mcp

# 重启服务
sudo systemctl restart deribit-mcp

# 查看实时日志
sudo journalctl -u deribit-mcp -f

# 查看最近 100 行日志
sudo journalctl -u deribit-mcp -n 100

# 设置开机自启
sudo systemctl enable deribit-mcp

# 禁用开机自启
sudo systemctl disable deribit-mcp
```

### systemd 服务文件详解

服务文件位于 `/etc/systemd/system/deribit-mcp.service`：

```ini
[Unit]
Description=Deribit MCP Server (HTTP/SSE)
Documentation=https://github.com/example/deribit-mcp-server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deribit
Group=deribit
WorkingDirectory=/opt/deribit-mcp

# 环境配置文件
EnvironmentFile=/etc/deribit-mcp/config.env

# 启动命令
ExecStart=/opt/deribit-mcp/venv/bin/python -m deribit_mcp.http_server

# 自动重启配置
Restart=always           # 总是重启（任何退出都重启）
RestartSec=5            # 重启前等待 5 秒
StartLimitIntervalSec=60 # 60 秒内
StartLimitBurst=5        # 最多重启 5 次（防止无限重启循环）

# 超时设置
TimeoutStartSec=30       # 启动超时 30 秒
TimeoutStopSec=30        # 停止超时 30 秒

# 资源限制
MemoryMax=512M          # 最大内存 512MB
CPUQuota=100%           # 最大 CPU 使用率

# 安全加固
NoNewPrivileges=yes     # 禁止获取新权限
ProtectSystem=strict    # 文件系统只读保护
ProtectHome=yes         # 禁止访问 /home
PrivateTmp=yes          # 使用私有 /tmp
ReadWritePaths=/var/log/deribit-mcp  # 允许写入日志目录

# 日志
StandardOutput=journal
StandardError=journal
SyslogIdentifier=deribit-mcp

[Install]
WantedBy=multi-user.target
```

### 自动重启机制说明

| 配置项 | 值 | 说明 |
|--------|------|------|
| `Restart=always` | always | 任何情况下退出都会重启 |
| `RestartSec=5` | 5秒 | 每次重启前等待时间 |
| `StartLimitIntervalSec=60` | 60秒 | 重启频率限制时间窗口 |
| `StartLimitBurst=5` | 5次 | 时间窗口内最大重启次数 |

如果服务在 60 秒内重启超过 5 次，systemd 会停止尝试重启，并将服务标记为 `failed`。

---

## 📊 健康检查与监控

### 手动健康检查

```bash
# 简单检查
curl http://localhost:8000/health

# 详细输出
curl -s http://localhost:8000/health | jq .

# 检查工具列表
curl -s http://localhost:8000/tools | jq '.tools | length'
```

### 自动健康检查（Cron）

设置定时健康检查和自动恢复：

```bash
# 编辑 root 的 crontab
sudo crontab -e

# 添加以下行（每 5 分钟检查一次）
*/5 * * * * /opt/deribit-mcp/scripts/healthcheck.sh >> /var/log/deribit-mcp/healthcheck.log 2>&1
```

健康检查脚本功能：
- 检查服务是否运行
- 检查 HTTP 端点是否响应
- 自动重启不健康的服务
- 记录日志

### Prometheus 监控（可选）

如果使用 Prometheus 监控，可以配置抓取健康检查端点：

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'deribit-mcp'
    scrape_interval: 30s
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: /health
```

---

## 🔄 更新应用

### 方式 1: 使用更新脚本（推荐）

```bash
# 进入项目目录
cd /path/to/deribit-mcp-server

# 拉取最新代码
git pull

# 运行更新脚本
sudo bash scripts/update.sh
```

### 方式 2: 手动更新

```bash
# 停止服务
sudo systemctl stop deribit-mcp

# 进入项目目录
cd /path/to/deribit-mcp-server

# 拉取最新代码
git pull

# 更新安装目录
sudo cp -r src /opt/deribit-mcp/
sudo cp pyproject.toml /opt/deribit-mcp/

# 更新依赖
sudo /opt/deribit-mcp/venv/bin/pip install -e /opt/deribit-mcp

# 修复权限
sudo chown -R deribit:deribit /opt/deribit-mcp

# 启动服务
sudo systemctl start deribit-mcp
```

---

## 🗑️ 卸载

```bash
# 运行卸载脚本
sudo bash scripts/uninstall.sh
```

卸载脚本会：
- 停止并禁用服务
- 删除 systemd 服务文件
- 删除应用目录
- 可选：保留配置和日志

---

## 🌐 网络配置

### 防火墙（UFW）

如果需要从外部访问：

```bash
# 允许 8000 端口
sudo ufw allow 8000/tcp

# 或者只允许特定 IP
sudo ufw allow from 192.168.1.0/24 to any port 8000

# 查看规则
sudo ufw status
```

### Nginx 反向代理（推荐生产环境）

创建 Nginx 配置：

```bash
sudo nano /etc/nginx/sites-available/deribit-mcp
```

配置内容：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 重定向到 HTTPS（推荐）
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL 证书（使用 Let's Encrypt）
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # SSE 配置
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/deribit-mcp /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 📁 目录结构

安装后的目录结构：

```
/opt/deribit-mcp/           # 应用目录
├── src/                    # 源代码
│   └── deribit_mcp/
│       ├── __init__.py
│       ├── analytics.py    # 分析计算
│       ├── client.py       # API 客户端
│       ├── config.py       # 配置管理
│       ├── http_server.py  # HTTP/SSE 服务器
│       ├── models.py       # 数据模型
│       ├── server.py       # stdio 服务器
│       └── tools.py        # MCP Tools
├── venv/                   # Python 虚拟环境
├── scripts/                # 管理脚本
│   ├── healthcheck.sh
│   ├── update.sh
│   └── uninstall.sh
└── pyproject.toml          # 项目配置

/etc/deribit-mcp/           # 配置目录
└── config.env              # 配置文件

/var/log/deribit-mcp/       # 日志目录
└── healthcheck.log         # 健康检查日志

/etc/systemd/system/
└── deribit-mcp.service     # systemd 服务文件

/etc/logrotate.d/
└── deribit-mcp             # 日志轮转配置
```

---

## 🐛 故障排除

### 问题 1: 服务无法启动

```bash
# 查看详细错误日志
sudo journalctl -u deribit-mcp -n 50 --no-pager

# 手动运行测试
sudo -u deribit /opt/deribit-mcp/venv/bin/python -m deribit_mcp.http_server
```

### 问题 2: Python 模块找不到

```bash
# 检查虚拟环境
/opt/deribit-mcp/venv/bin/pip list | grep deribit

# 重新安装
sudo /opt/deribit-mcp/venv/bin/pip install -e /opt/deribit-mcp
```

### 问题 3: 权限错误

```bash
# 重置权限
sudo chown -R deribit:deribit /opt/deribit-mcp
sudo chown -R deribit:deribit /var/log/deribit-mcp
sudo chmod 600 /etc/deribit-mcp/config.env
```

### 问题 4: 端口被占用

```bash
# 查看 8000 端口占用
sudo lsof -i :8000

# 或者
sudo ss -tlnp | grep 8000

# 修改配置使用其他端口
sudo nano /etc/deribit-mcp/config.env
# 修改 DERIBIT_PORT=8001
sudo systemctl restart deribit-mcp
```

### 问题 5: 连接 Deribit API 失败

```bash
# 测试网络连接
curl -v https://www.deribit.com/api/v2/public/get_time

# 检查 DNS
nslookup www.deribit.com

# 检查防火墙
sudo iptables -L -n | grep -i drop
```

---

## 📞 API 端点参考

安装后可用的 API 端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/tools` | GET | 列出所有工具 |
| `/tools/call` | POST | 调用工具 |
| `/sse` | GET | SSE 连接（MCP） |
| `/mcp/sse` | GET | SSE 连接（备用） |
| `/messages` | POST | MCP 消息 |
| `/mcp/messages` | POST | MCP 消息（备用） |
| `/diagnostics` | GET | 诊断信息 |
| `/test` | GET | 连接测试 |

---

## 🔗 MCP 客户端配置

### Cursor 配置

```json
{
  "mcpServers": {
    "deribit": {
      "transport": "sse",
      "url": "http://your-server:8000/sse"
    }
  }
}
```

### CherryStudio 配置

```json
{
  "mcpServers": {
    "deribit": {
      "transport": "sse", 
      "url": "http://your-server:8000/sse"
    }
  }
}
```

---

## 📝 日志查看

```bash
# 实时查看服务日志
sudo journalctl -u deribit-mcp -f

# 查看今天的日志
sudo journalctl -u deribit-mcp --since today

# 查看特定时间范围
sudo journalctl -u deribit-mcp --since "2024-01-01 00:00:00" --until "2024-01-01 23:59:59"

# 只看错误日志
sudo journalctl -u deribit-mcp -p err

# 查看健康检查日志
sudo tail -f /var/log/deribit-mcp/healthcheck.log
```
