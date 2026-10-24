# 发布清单

发布到 [AstrBot 插件市场](https://cloud.astrbot.app/publish) 前，按这个顺序走一遍。

## 0. 安全前提

- **不要把账号密码、验证码、Personal Access Token 发给任何人**，包括 AI 助手。
  别人拿到密码就等于拿到你的邮箱、QQ、GitHub（如果复用）以及绑在上面的所有东西。
- 发布不需要任何人替你登录：`git push` 时 git 会自己弹浏览器或系统凭据管理器，
  由你本人在 GitHub 页面上完成认证，凭据不会经过第三方。
- 如果密码已经在聊天里出现过，**立刻改掉**，并给 GitHub / 邮箱开两步验证。

## 1. 替换占位符

仓库里目前用 `your-github-name` 占位，一共 4 处：

| 文件 | 位置 |
| --- | --- |
| `metadata.yaml` | `author` |
| `metadata.yaml` | `repo` |
| `metadata.yaml` | `social_link` |
| `LICENSE` | `Copyright (c) 2026 ...` |

```bash
# 把 your-github-name 换成你的 GitHub 用户名，例如 octocat
```

改完后跑自检：

```bash
python scripts/preflight.py
```

它会检查文件齐全、`metadata.yaml` 字段、版本号格式、占位符残留、配置 schema、logo 尺寸、
README 徽章与 CHANGELOG 是否同步。**全部 ✓ 再去提交。**

`author` 不能包含 `/`，插件身份是 `author/name`，发布后改名会导致市场更新对不上。

## 2. 建仓库

- 仓库名：`astrbot_plugin_psycare`（与 `metadata.yaml` 的 `name` 一致）
- 可见性：Public（市场安装走 GitHub 公开拉取）
- 许可证：MIT（仓库里已有 `LICENSE`）

### 方式 A：命令行

```bash
cd <本目录>
git init
git add .
git commit -m "feat: 心理关怀 1.2.0"
git branch -M main
git remote add origin https://github.com/<你的用户名>/astrbot_plugin_psycare.git
git push -u origin main
git tag v1.2.0
git push origin v1.2.0
```

第一次 `git push` 会要求认证：选浏览器登录，或用系统凭据管理器保存的 token。
**不要**把 token 贴到命令行参数里，也不要发给别人。

### 方式 B：不用命令行

1. 在 GitHub 网页新建 Public 仓库 `astrbot_plugin_psycare`（不要勾选 README）。
2. 用 [GitHub Desktop](https://desktop.github.com/)：`File → Add local repository` 选中本目录 →
   `Publish repository`（取消勾选 "Keep this code private"）。
3. 或者网页上逐个拖拽上传，但文件多，不推荐。

> 仓库根目录必须直接是插件本体：`main.py`、`metadata.yaml`、`_conf_schema.json`
> 都在根上。AstrBot 安装时会以仓库根作为插件目录。

## 3. 打包自检

```bash
python tests/test_plugin.py        # 77 项断言应全部通过
python scripts/build_release.py    # 生成 dist/*.zip，检查体积与必需文件
```

`build_release.py` 会打印文件数、体积（上限 16MB）、缺失项和未替换的占位符。

## 4. 提交市场

1. 打开 <https://cloud.astrbot.app/publish>，用 GitHub 登录（你自己点授权）。
2. 填仓库地址，平台会读取 `metadata.yaml`。
3. 核对展示信息：`display_name`、`short_desc`、`desc`、`tags`、`support_platforms`。
4. 确认 `logo.png`（256×256，仓库根目录已有一张）。
5. 提交，等待审核。

## 5. 后续版本

1. 改 `metadata.yaml` 的 `version`（语义化版本，不带 `v` 前缀，例如 `1.2.1`）。
2. 在 `CHANGELOG.md` 顶部加一段。
3. 跑 `preflight.py` + 测试 → `git tag v1.2.1` → 推送 tag。
4. 市场按 `metadata.yaml` 的 `version` 与记录比对，推 tag 后即可更新。

## 常见退回原因

| 现象 | 原因 |
| --- | --- |
| 安装后插件不出现 | 仓库根没有 `main.py` 或 `metadata.yaml` |
| 市场里更新对不上 | `author`/`name` 改过，`plugin_id` 变了 |
| 审核提示包过大 | zip 超过 16MB，压缩 `docs/` 里的截图 |
| 版本校验失败 | `version` 写成 `v1.2.0`，市场规范要求纯语义化版本 |
| 加载即报错 | `astrbot_version` 限制过严，或依赖没写进 `requirements.txt` |
