# Deribit MCP Server 快速参考

## 🚀 一键安装

```bash
git clone https://github.com/your-repo/deribit-mcp-server.git
cd deribit-mcp-server
sudo bash scripts/install.sh
```

## 🔧 服务管理

| 操作 | 命令 |
|------|------|
| 查看状态 | `sudo systemctl status deribit-mcp` |
| 启动服务 | `sudo systemctl start deribit-mcp` |
| 停止服务 | `sudo systemctl stop deribit-mcp` |
| 重启服务 | `sudo systemctl restart deribit-mcp` |
| 查看日志 | `sudo journalctl -u deribit-mcp -f` |
| 编辑配置 | `sudo nano /etc/deribit-mcp/config.env` |

## 📍 重要路径

| 路径 | 说明 |
|------|------|
| `/opt/deribit-mcp/` | 应用安装目录 |
| `/etc/deribit-mcp/config.env` | 配置文件 |
| `/var/log/deribit-mcp/` | 日志目录 |
| `/etc/systemd/system/deribit-mcp.service` | systemd 服务文件 |

## 🌐 API 端点

| 端点 | 说明 |
|------|------|
| `http://localhost:8000/health` | 健康检查 |
| `http://localhost:8000/tools` | 工具列表 |
| `http://localhost:8000/sse` | SSE 连接 (MCP) |
| `http://localhost:8000/tools/call` | 调用工具 |

## ✅ 健康检查

```bash
# 快速测试
curl http://localhost:8000/health

# 详细输出
curl -s http://localhost:8000/health | jq .
```

## 🔄 更新应用

```bash
cd /path/to/deribit-mcp-server
git pull
sudo bash scripts/update.sh
```

## 🗑️ 卸载

```bash
sudo bash scripts/uninstall.sh
```

## 📝 配置示例

```bash
# /etc/deribit-mcp/config.env

DERIBIT_ENV=prod
DERIBIT_ENABLE_PRIVATE=false
DERIBIT_CLIENT_ID=your_client_id
DERIBIT_CLIENT_SECRET=your_client_secret
DERIBIT_HOST=0.0.0.0
DERIBIT_PORT=8000
DERIBIT_DRY_RUN=true
```

## 🛠️ MCP 客户端配置

### Cursor / CherryStudio (SSE)

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

## 🐛 故障排除

```bash
# 查看详细日志
sudo journalctl -u deribit-mcp -n 100 --no-pager

# 手动测试运行
sudo -u deribit /opt/deribit-mcp/venv/bin/python -m deribit_mcp.http_server

# 检查端口占用
sudo lsof -i :8000

# 重置权限
sudo chown -R deribit:deribit /opt/deribit-mcp
```

## 📊 自动重启配置

systemd 配置确保服务自动重启：

- `Restart=always` - 任何退出都重启
- `RestartSec=5` - 重启前等待 5 秒
- `StartLimitBurst=5` - 60 秒内最多重启 5 次

## 🔒 安全建议

1. 不要将 API 密钥提交到代码仓库
2. 使用 `chmod 600` 保护配置文件
3. 生产环境使用 Nginx + HTTPS
4. 限制防火墙端口访问
