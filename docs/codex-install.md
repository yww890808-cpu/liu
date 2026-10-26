# 用 Codex 一键安装 / 更新

让电脑上的 Codex 代你执行安装命令。GitHub 上每个代码框右上角都有**复制按钮**，点一下即可复制整段。

## 安装前准备

| 准备什么 | 必须吗 | 说明 |
|---|---|---|
| 一台电脑（Windows 或 Mac） | 必须 | 内存至少 8G；系统要一直开着才能自动拉单、推库存 |
| Docker Desktop | 必须 | https://www.docker.com/products/docker-desktop/ 下载安装，**装好后打开**，等显示 Running（需要管理员权限，Codex 装不了） |
| 本地版 Codex | 必须 | 命令行版、编辑器插件或桌面版均可；网页上的云端 Codex 装不到你的电脑上 |
| 一个空文件夹 | 必须 | 例如 `ozon`，让 Codex 在里面工作 |
| Ozon 的 Client-Id 和 Api-Key | 装完再用 | Ozon 卖家后台 → 设置 → API 密钥。装好后在管理页面里填写，不用给 Codex |
| MPStats token | 可选 | 只有选品功能需要，没有不影响其他功能 |

## 第一次安装：把下面整段发给 Codex

```text
请帮我在这台电脑上安装并启动一个项目，按顺序做，每一步告诉我结果，遇到报错先停下来告诉我原因，不要自己乱改。

1. 运行 docker info 检查 Docker 是否在运行。没有运行就停下来提醒我打开 Docker Desktop。
2. 在当前文件夹下载代码并进入：
   git clone https://github.com/yww890808-cpu/liu.git
   cd liu
   如果 liu 文件夹已经存在，改为进入 liu 后执行 git pull，然后直接跳到第 5 步。
3. 把 .env.example 复制一份，命名为 .env。
4. 运行下面这条命令两次，得到两串随机字符：
   docker run --rm python:3.11-slim python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   第一串填到 .env 的 FERNET_KEY= 后面，第二串填到 JWT_SECRET= 后面。
   其他配置不要改，DRY_RUN 必须保持 true。
5. 启动：docker compose up -d --build（第一次需要几分钟）
6. 等 30 秒后检查：
   - docker compose ps 里 6 个服务（postgres、redis、api、worker、beat、backup）都在运行
   - 访问 http://localhost:8000/health 返回 "status":"ok"
   有服务没起来就运行 docker compose logs api 把报错告诉我。
7. 完成后提醒我：把 .env 文件另外备份一份到安全的地方，里面的 FERNET_KEY 丢了会导致已保存的店铺密钥无法解密。

注意：
- 不要修改项目里的任何代码文件
- 不要在回复里显示 .env 中的密钥内容
- 不要执行 git commit、git push
```

## Codex 做完后你自己做

1. **打开后台**：电脑浏览器打开 http://localhost:8000 ，直接进入，不需要用户名和密码。
2. **接入真实店铺**：按 [首次接入真实店铺检查清单](go-live-checklist.md) 逐项进行，确认无误前保持试运行模式。

## 以后更新到新版本：发给 Codex

```text
在 liu 文件夹里执行 git pull，然后运行 docker compose up -d --build，不要改动 .env 文件。完成后用 docker compose ps 确认所有服务都在运行，并告诉我结果。
```

店铺、订单、库存、选品数据都保存在本地数据库里，更新代码不会丢失。

## 配置选品（可选）：发给 Codex

先在 MPStats 个人中心复制 API token，然后把下面这段里的 `你的token` 替换掉再发：

```text
在 liu 文件夹的 .env 文件中，把 MPSTATS_TOKEN= 这一行改为 MPSTATS_TOKEN=你的token（其他行不要动），然后运行 docker compose up -d。不要在回复里显示 token。
```

## 需要登录时（可选）

默认免登录，能打开网址的人都能操作店铺（同一 Wi-Fi 下的设备也能访问）。想加上登录，发给 Codex：

```text
在 liu 文件夹的 .env 文件中，把 LOGIN_REQUIRED=false 改为 LOGIN_REQUIRED=true（其他行不要动），然后运行 docker compose up -d。完成后提醒我自己运行 docker compose exec api python -m app.cli create-user admin 创建账号。
```

## 遇到问题

把 Codex 显示的报错原文发给开发者。常见问题见 [部署说明 → 常见问题](deploy.md#常见问题)。
