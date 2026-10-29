# 从 v0.10.2 升级到 v0.11.0

本版没有增加/删除 SQL 表或列，没有改动已确认卡片/报告的存储身份；新增可选 JSON 字段与缓存命名空间。现有 Alembic 初始化照常运行。旧卡片、游戏导入、画像、历史任务及报告保持可读，旧报告不会按新分数自动重写。

## 升级步骤

1. 停止旧服务，保留整个旧目录。不要覆盖旧源码安装，先在独立新目录解压 v0.11。
2. 确认原数据库路径：默认是旧目录的 `data/gamereach.db`；若 `.env` 指定 `DATABASE_URL`，以实际路径为准。不要误备份测试数据库。
3. 使用新版本的只读源备份脚本。以下从新项目根目录执行，替换旧路径；输出必须尚不存在。

Windows PowerShell：

```powershell
py -3 .\scripts\upgrade_v011.py --source 'C:\GameReach-old\data\gamereach.db' --output '.\backups\before-v011.db'
py -3 .\scripts\upgrade_v011.py --source '.\backups\before-v011.db' --output '.\data\gamereach.db'
Copy-Item 'C:\GameReach-old\.env' '.\.env'
```

Linux / macOS：

```bash
python3 scripts/upgrade_v011.py --source /path/to/old/data/gamereach.db --output backups/before-v011.db
python3 scripts/upgrade_v011.py --source backups/before-v011.db --output data/gamereach.db
cp /path/to/old/.env .env
```

不存在旧 `.env` 时无需复制；不要把示例文件覆盖现有配置。若新目录已有 `.env` 或目标DB，先核对并保留，不删除后盲目重试。源为SQLite只读连接，脚本执行完整性检查、SQLite backup（包含一致的WAL内容）并输出各表数量；拒绝覆盖已有输出。

4. 原 `.env` 如使用绝对数据库路径，改为新副本路径，仅调整路径、不改变密钥。若使用外部数据库（本次未验收），采用该数据库自身备份流程；本脚本只接受SQLite文件。
5. 按平台启动说明运行。建议先用独立 `DATABASE_URL=sqlite:///./data/v011-smoke.db` 及 fixtures/mock 验证环境；不要用演示库代替迁移后的真实资料。实际使用时恢复自己确认的数据/模型配置，不默认启用付费服务。
6. 核对原有卡片、导入、历史任务/报告；旧任务保留原快照。升级中不要让新旧服务同时写同一DB。新卡片生成不会自动重写旧确认内容。

## 缓存与进行中的任务

来源TTL和版本校验保留。卡片缓存/核验协议升级为新版本，因此首次新研究可能需要重新核验，旧报告仍保留。任务状态结构未改：已有暂停/人工节点可继续按原API操作；自动运行中的任务应先暂停/结束再升级。已完成报告不会因重排被覆盖。回归验证包括暂停/恢复、来源失效、核验缓存复用和纯权重本地重排。

## 回退

停止新版；使用保留的原版目录和升级前备份。恢复到**新的**文件名以便核对：

```bash
python3 scripts/upgrade_v011.py --restore --source backups/before-v011.db --output backups/restored-v0102.db
```

Windows将 `python3` 换为 `py -3`，路径按PowerShell格式写。核对脚本输出后，让旧版本 `DATABASE_URL` 指向恢复文件，并保留新版DB副本。回退至升级前备份不包含升级后新增任务/编辑；这些仍在新版DB中，避免直接覆盖而丢失。不要假设旧解析器可完整理解新版 JSON 并直接降级同一DB。

## 本次实际验证

以未修改 v0.10.2 源码创建合成用户数据库，再用新版副本读取：已确认卡片原文、存储画像、旧API字段、导入、历史报告均一致；旧卡片可编辑；新任务运行；回退各表数量一致，8/8检查通过。见 `validation_v011/migration.json`。这是实际旧版DB兼容实验，不是用户真实数据库验收；后者需要升级时核对。
