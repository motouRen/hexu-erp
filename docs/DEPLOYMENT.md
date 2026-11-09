# 启动与部署

v0.2 公开演示与普通经营环境分开，见 [演示说明](DEMO.md)。恢复演练和旧版升级命令见 [v0.2 说明](V0.2.md)。在线临时演示的 Waitress 仅监听回环地址，在专用 HTTPS 连接入口后使用 `--url-scheme=https`，应用关闭 DEBUG；不可将这一专用设置照搬到可被公网直接访问的 HTTP 监听端口。

## 本地源码运行

README 的启动脚本创建专用虚拟环境、安装锁定依赖、迁移数据库，再幂等初始化演示。演示含两个门店、三个渠道、八种虚构商品与七天业务记录。它不会删除、重置或覆盖已有业务与密码。可通过环境变量 `ERP_ADMIN_USERNAME`、`ERP_ADMIN_PASSWORD` 设置初始管理员；密码至少 12 字符，变量应通过安全环境配置提供。

忘记密码时，在本地管理终端交互执行 `python manage.py changepassword admin`。`LOCAL_ACCESS.txt` 仅记录初始化时的密码，修改密码后该文件不会自动更新。

## PostgreSQL 本地集成

需要 Docker Engine 和 Compose。本机交付环境未安装 Docker，因此以下容器配置未实际构建运行。

1. 在项目目录创建 `.env`，设置随机的 `ERP_DB_PASSWORD`。Compose 会读取该文件；应用自身不会自动读取 `.env`。
2. 执行 `docker compose -f compose.local.yml up --build`。
3. 首次初始化后执行 `docker compose -f compose.local.yml cp web:/app/LOCAL_ACCESS.txt ./LOCAL_ACCESS.txt`，查看新生成的演示账号。
4. 打开 <http://127.0.0.1:8765/>。

这是 PostgreSQL 开发环境，仍运行 Django 开发服务器并仅映射回环地址，不作为公网生产配置。数据库保存在命名卷 `hexu_pg`，普通停止不会删除该卷。不要对有实际数据的卷执行删除操作。若重建容器后本地账号文件丢失，使用 `docker compose -f compose.local.yml exec web python manage.py changepassword admin` 交互重设。

## 生产部署参考

生产采用 PostgreSQL、Gunicorn 与 HTTPS 反向代理。必须明确设置：

| 环境变量 | 用途 |
| --- | --- |
| ERP_DEBUG=0 | 关闭调试，强制 PostgreSQL、HTTPS 和安全 Cookie |
| ERP_SECRET_KEY | 至少 50 字符的独立随机密钥，跨重启保留 |
| ERP_ALLOWED_HOSTS | 用逗号分隔真实域名，无协议前缀 |
| ERP_DB_HOST / PORT / NAME / USER / PASSWORD | PostgreSQL 连接配置，运行账号使用最小权限 |
| ERP_TRUST_PROXY=1 | 仅在受控反向代理确实覆盖 X-Forwarded-Proto 且后端不可被外部直连时启用 |

安装 `requirements.lock.txt` 和 `requirements-production.txt`。在环境变量已经正确注入的终端执行：

```sh
python manage.py migrate --noinput
python manage.py init_roles
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py check --deploy
gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2
```

不运行 `init_demo`，生产模式会拒绝演示初始化。`Dockerfile` 提供 Gunicorn 应用镜像，但迁移、静态资源收集和管理员创建仍需由部署步骤显式执行；它不是已经验证的完整生产栈。WhiteNoise 负责已收集的静态资源；反向代理负责 TLS、连接限制与登录限速。生产依赖包含版本范围，应在目标环境构建通过后保存完整部署锁定清单。

业务校验失败是 400/409；查看服务日志时记录请求路径、异常和时间，避免记录密码、CSRF、Cookie 或完整客户资料。`/health/` 验证应用及数据库可用，不检查外部平台，因为首版尚未连接平台。

## 备份与恢复

SQLite 本地备份前停止开发服务，再复制数据库和对应的本地配置到私有备份目录。PostgreSQL 使用数据库工具备份并保留对应应用版本和迁移版本。备份文件含业务数据，不提交 Git。

正式使用前需在隔离数据库验证恢复，核对：库存余额与流水合计、占用与待发货订单、订单收退款累计、用户权限。当前仓库未执行真实生产备份恢复演练。
