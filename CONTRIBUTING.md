# 贡献指南

欢迎提交复现明确的缺陷、测试和边界清楚的功能改进。讨论和文档可使用中文，代码标识符使用英文。

## 本地开发

按 README 启动后，先运行 `python manage.py test`。数据库模型变更须提交迁移；涉及库存或资金的行为先在 `core/services.py` 实现，API 负责输入和权限，不在界面里维护另一份业务余额。

新增功能请写清触发条件、改变的数据、失败时的回滚方式及验证方法。库存相关改动必须考虑占用、取消和退货；资金相关改动必须保留原单与原交易记录。

提交前运行：

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
node --check static/app.js
```

并发相关改动还需在 PostgreSQL 下运行测试。SQLite 跳过的行锁用例不能算作并发验证通过。

请勿提交数据库、真实订单、客户信息、密码、平台令牌、浏览器登录状态或 `.env`。可使用 `examples/` 内虚构数据。提交贡献表示愿意按照项目 Apache-2.0 许可证提供该贡献；新增依赖需说明来源、许可证和理由。

## 报告问题

附版本、运行环境、最小复现步骤、期望结果、实际结果和脱敏日志。安全问题的报告规则见 SECURITY.md。
