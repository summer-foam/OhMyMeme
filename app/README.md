# OhMyMeme

轻量化跨平台表情包管理系统 — 突破表情包上限，快捷键呼出、搜索即复制。

### **QQ交流群：891636253**

![picture](https://raw.githubusercontent.com/OhMyMeme/OhMyMeme/refs/heads/dev/resource/picture.gif)

## 功能

- **系统托盘运行** — 最小化资源占用，后台常驻
- **单实例运行** — 重复启动自动检测已有实例并退出，避免数据冲突
- **全局快捷键** — 默认 `Ctrl+Alt+N` 呼出/隐藏主面板；热键失效自动修复，无需重启软件
- **表情管理** — 导入 / 搜索 / 标签 / 收藏 / 多级分组（最多 3 层）
- **悬停放大预览** — 鼠标悬停 0.5s 浮出完整原图，设置页可关闭
- **一键复制** — 点击表情包自动复制到剪贴板（GIF 保留动画）；快捷键呼出后复制成功自动隐藏
- **批量操作** — 多选模式下批量加组 / 移动 / 打标签 / 删除
- **原生文件拖拽** — 关闭拖拽排序后，表情可直接拖到 QQ/微信等外部应用
- **复制处理** — 超限静态图按四种模式处理（不处理 / WebP 缩放 / 转 GIF / 转 GIF 隐写原图可无损还原）；可避免输出 WebP 以兼容微信等应用
- **多种导入** — 窗口拖放、文件夹导入（可自动建同名分组）
- **右键菜单** — 重命名 / 收藏 / 打标签 / 添加分组 / 删除；卡片悬停快捷收藏
- **标签** — 多标签交集筛选，搜索同时匹配文件名与标签名
- **GIF 动图** — 网格内自动播放，设置中可关闭
- **分组筛选** — 收藏夹/自定义分组，可与标签、搜索叠加；「未分类」分组自动汇总未分组表情
- **侧边栏滑动手势** — 左缘右滑展开、侧栏内左滑折叠
- **本地缓存** — 缩略图+原图双层缓存，离线可用；启动自动扫描去重
- **导入限制** — 超过 2K 分辨率或 20MB 的表情导入时弹窗选择是否保留；设置中可关闭大小限制
- **自定义存储位置** — 可更换图片存放目录，切换时自动迁移现有文件
- **本地备份与恢复** — 一键导出全量备份 ZIP，新设备一键恢复
- **远程同步** — FTP / S3 / R2 / WebDAV 多端同步，实时进度与速度；上传前按云端清单比对，仅上传缺失/变更的表情
- **同步状态指示** — 标题栏云图标显示已同步 / 缺失文件数；后台传输时显示进度环与剩余文件，点击查看详情
- **云端直接使用** — 本地未下载的云端表情带云角标显示，点击即下载并使用（默认开启）
- **局域网互联** — 与同局域网的手机版配对，互相同步表情包与配置
- **多源导入** — 手机 QQ 表情（ADB 拉取）、电脑版 QQ（QQNT）收藏、Telegram、微信电脑版收藏、抖音自定义表情
- **插件系统** — 第三方插件扩展主窗口与设置页，可单独启用/停用
- **无边框窗口** — 自定义标题栏，可选允许拖边框调整窗口大小
- **自动更新** — 启动时与每日自动检测新版本，一键升级

## 快速开始

### 下载

从 [Releases](https://github.com/OhMyMeme/OhMyMeme/releases/latest) 下载对应系统的安装包或可执行文件，直接运行。

Windows 除 `.exe` 安装包外另提供 `.msi`（WiX，适合离线/组策略批量部署）与 `.msix`（Microsoft Store 上架 / 侧载）两种格式。

隐私策略见 [docs/privacy.md](docs/privacy.md)。

### 从源码运行

**环境要求**: Python 3.10+

```bash
git clone https://github.com/OhMyMeme/OhMyMeme.git
cd ohmymeme
pip install -r requirements.txt
python -m src
```

- 主窗口前端为 Vue 3，源码运行时若构建产物缺失会自动执行一次 `npx vite build`
- Linux 额外需要 GTK/WebKit 依赖（`python3-gi` + `gir1.2-webkit2-*`），详见 [CONTRIBUTING.md](CONTRIBUTING.md)

或使用 conda：

```bash
conda create -n ohmymeme python=3.12
conda activate ohmymeme
pip install -r requirements.txt
python -m src
```

## 使用

### 基本操作

1. **启动** — 运行后系统托盘出现蓝色图标，按 `Ctrl+Alt+N` 呼出主面板
2. **导入** — 点击标题栏「导入」按钮，或直接拖拽图片到窗口（支持 png/jpg/gif/webp）
3. **复制** — 点击任意表情包自动复制到剪贴板（GIF 保留动画）
4. **搜索** — 搜索框输入关键词实时筛选（同时匹配文件名与标签名）；点击标签或分组叠加过滤
5. **右键菜单** — 重命名 / 收藏 / 打标签 / 添加分组 / 删除

### 收藏与分组

- 右键 →「收藏」将表情加入收藏夹，点击左上角⭐按钮筛选收藏内容
- 右键 →「添加到分组」创建或选择已有分组；分组显示在搜索栏下方，点击即筛选
- 右键分组可新建小分组（最多 3 层）、重命名或删除

### 设置

点击标题栏⚙按钮打开设置页，主要选项：

在「基础设置 → 窗口 → 主题」选择浅色或深色，保存后主窗口与设置窗口立即切换；重启后保留选择，恢复默认设置会切回深色。 保存失败时会提示重试；连续保存与恢复默认设置按提交顺序执行，旧版主窗口回退页面也支持主题。

| 选项 | 说明 |
|------|------|
| **快捷键** | 自定义全局热键，格式如 `Ctrl+Shift+M` |
| **热键在鼠标处显示** | 默认关闭，仅 Windows；热键呼出隐藏面板时放在鼠标所在显示器 |
| **开机自启** | 系统登录时自动启动，可选静默启动（仅托盘） |
| **允许调整窗口大小** | 默认关闭；开启后拖动边框调整窗口大小，尺寸自动记忆 |
| **GIF 动画** | 关闭后网格中仅显示 GIF 首帧 |
| **复制处理** | 复制超限静态图的处理模式（不处理 / WebP 缩放 / 转 GIF / 转 GIF 隐写原图） |
| **复制时避免 WebP** | 默认关闭；开启后复制产物不含 WebP，兼容微信等把 WebP 当文件的应用 |
| **同步进度/提示** | 上传/下载进度条与完成提示，可分别开关 |
| **导入** | 从手机（ADB）/ 从电脑（QQNT）/ Telegram / 抖音 / 微信 |
| **局域网互联** | 设置端口与连接密钥，开启后同局域网手机版可发现本机 |
| **远程同步** | 配置 FTP / S3 / R2 / WebDAV 同步 |
| **云端同步** | 云端直接使用、标签/收藏写入清单、缺失缩略图自动补传等开关 |
| **备份与恢复** | 备份目录、创建备份、备份列表管理、从 ZIP 恢复 |
| **插件** | 查看已安装插件、修改插件设置、启用/停用 |
| **危险操作** | 一键清空本地/云端全部表情包（需双重确认） |
| **关于** | 版本号与检查更新、GitHub 地址、QQ 群入口、贡献者名单 |

### 插件

把插件文件夹放入插件目录（设置页「打开插件目录」直达），目录内含 `plugin.json`，重启软件后加载。开发期可把插件路径加入 `config.json` 的 `plugin_dirs` 数组直连，免复制。

- 插件开发：见[插件模板仓库](https://github.com/OhMyMeme/omm-plugin-template)
- 官方[个性化插件](https://github.com/OhMyMeme/omm-personalized-plugin)可自定义启动动画、背景、字体与顶栏按钮布局

### 远程同步

支持 FTP / S3 / R2 / WebDAV 四种后端同步多台设备的表情包库。配置完成后点击「测试连接」验证，然后使用标题栏的⬆上传 / ⬇下载按钮同步。

标题栏云图标会显示当前同步状态：绿色对勾表示本机与云端一致，黄色感叹号表示存在差异；未配置云端时图标自动隐藏。点击图标可查看「本地缺失 / 云端缺失」文件数，也可手动重新检查云端清单。上传或下载转入后台后，图标变为传输样式并在外圈显示进度环，点击可查看剩余文件数、速度与当前文件。

> ⚠️ 若设置中开启了「同步时删除远程文件」，上传操作会删除远程端已不存在的文件。

**云端直接使用**（默认开启）：本地未下载的云端表情与本地表情穿插显示在网格中（左下角云角标），点击云卡片即下载、校验并自动复制使用。

### 局域网互联

与同一局域网内的手机版 OhMyMeme 配对，无需公网即可互相同步表情包与配置：

1. **配置** — 设置页「局域网互联」设置端口（默认 17852）与连接密钥
2. **开启** — 勾选「开启互联访问」临时启动服务（重启后默认关闭）
3. **连接** — 手机版扫描发现本机并输入密钥配对，电脑端确认设备信息后开始同步

> ⚠️ 服务可被同一局域网内所有设备探测到，仅在你信任的 Wi-Fi 下开启。

### 路径说明

| 用途 | Windows | 其他平台 |
|------|---------|----------|
| 配置文件 | `%APPDATA%/OhMyMeme/config.json` | `~/.config/OhMyMeme/config.json` |
| 数据库 | `%LOCALAPPDATA%/OhMyMeme/memes.db` | `~/.local/share/OhMyMeme/memes.db` |
| 缓存原图 | `%LOCALAPPDATA%/OhMyMeme/cache/` | `~/.local/share/OhMyMeme/cache/` |
| 缩略图 | `%LOCALAPPDATA%/OhMyMeme/thumbnails/` | `~/.local/share/OhMyMeme/thumbnails/` |
| 同步清单 | `%LOCALAPPDATA%/OhMyMeme/meme-index.json` | `~/.local/share/OhMyMeme/meme-index.json` |
| 插件目录 | `%LOCALAPPDATA%/OhMyMeme/plugins/` | `~/.local/share/OhMyMeme/plugins/` |

macOS 对应 `~/Library/Application Support/OhMyMeme/`（配置）与 `~/Library/Caches/OhMyMeme/`（数据）。

## 构建

```bash
pip install pyinstaller
python scripts/build.py   # 自动检测当前系统打包
```

> **⚠️ 注意**: PyInstaller **不支持交叉编译**，Windows/Linux/macOS 各平台需在自己的系统上构建。
> 其他参数（`--windows`/`--linux`/`--macos`/`--package` 等）与打包细节见 [CONTRIBUTING.md](CONTRIBUTING.md)。
> 也可使用 [GitHub Actions](https://github.com/OhMyMeme/OhMyMeme/actions)（手动运行 `Release`/`Build` workflow）。

## PR 贡献

欢迎提交 Pull Request。提交前请确保通过以下检查：

```bash
ruff check src/       # lint 检查
black --check src/    # 格式检查
python -m pytest tests/ -v   # 测试
```

CI 会自动运行 lint+test，更多贡献指引见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 贡献者

[![Contributors](https://contributor.starsfire.top/OhMyMeme/OhMyMeme/)](https://github.com/OhMyMeme/OhMyMeme/graphs/contributors)

## AI 辅助开发

本项目包含 `AGENTS.md` 文件，供 AI 编码助手读取以了解项目结构、代码规范和关键实现细节。若通过 AI 修改代码，请确保 AI 读取该文件后再进行操作。

## Star History

<a href="https://www.star-history.com/?repos=ohmymeme%2Fohmymeme&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ohmymeme/ohmymeme&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ohmymeme/ohmymeme&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ohmymeme/ohmymeme&type=date&legend=top-left" />
 </picture>
</a>

## 许可证

GPL-3.0
# 本地改造：QQ 收藏管理

本副本基于 OhMyMeme dev `ee7e38b8028954a6454f03df2f7e0ba7b8f88fe7`，保留上游 GPL-3.0 许可及署名。

主窗口新增「QQ 收藏」入口。在本地表情库开启多选、选中图片后，可进入面板上传到 QQ 收藏；面板支持拉取、搜索和选择 QQ 收藏，以及确认后批量删除。每项上传、删除都回查 QQ 列表后才显示成功，未确认项需先刷新核对，不会自动重传。取消仅停止未开始的项目。

上传前会显示本地所选图片的名称、缩略图、数量和目标 QQ 账号，确认后才创建任务。预览不会上传；执行时会再次检查原图是否存在。QQ 收藏分页显示，选择当前页与跨页已选数量分别呈现；连接、扫码及手工配置集中在独立设置区。

本地表情库与 QQ 收藏共用主窗口导航，通过横向滑动过渡切换，也支持在内容空白处触摸横滑。切换保留本地筛选、选择和滚动状态；后台任务继续执行。系统开启减少动态效果时关闭滑动动画。设置、图片预览与操作确认仍使用独立弹层。

Windows 在窗口初始化前启用逐显示器 DPI 感知，支持的系统优先使用 Per-Monitor V2。切页结束会清除合成变换，减少不同缩放比例下的文字模糊；快速反向、调整窗口尺寸及减少动态效果会正确结束过渡。DPI 设置需要重启应用生效，已经由宿主配置 DPI 的进程不会强行覆盖。

Windows 原生 DPI 消息会同步调整主窗口和设置窗口的物理尺寸，保持跨屏逻辑内容大小；已在 150% 与 100% 实际双屏之间程序移动往返验证，手工拖动和主观清晰度仍需实际使用确认。

任务结果分别统计已核验、已存在、失败和待核对。任务中存在错误不代表进度读取失败；结果仍可查看。只有无法读取有效进度时才保持操作保护锁，并提供重新检查任务的入口。

可使用内置管理器启动 NapCat，也可连接自行运行且已登录的 NapCat：启用仅绑定本机回环地址的 OneBot HTTP 服务，在面板填写地址、访问令牌和预期 QQ 号。写操作会再次检查账号。地址和预期账号保存在配置中，令牌仅在当前进程内存中，手工连接重启后需重新输入；收藏不会被自动删除。

本地标签、分组和排序不回写 QQ。当前列表完整性仍标记为未知，尚未处理服务端数量上限及分页。

### 后台启动与扫码登录

运行模式可选择 **Shell 后台模式** 或 **Framework 桌面模式**。Framework 需选择原始 `NapCat.Framework` 发布包，通过包内 `napimain.exe` / `napiloader.dll` 加载器启动桌面 QQ；QQ 窗口可正常使用，收藏工具复用其会话。此模式不附加到已运行的普通 QQ，也不修改 QQ 安装入口；登录是否需要扫码由 QQ 决定。同账号已在线时需自行退出旧实例。

运行中不能切换模式。停止 Framework 或退出工具会关闭由本工具启动的桌面 QQ，请先结束聊天操作；现有外部 QQ 不受控制。Shell 模式仍使用原始 Shell 发布包。两种模式均复制到私有运行目录，停止确认进程退出后清理运行副本。

QQ 收藏面板提供后台管理：选择已解压的原始 NapCat 发布包、检查 QQ 路径、启动、扫码、接入收藏和停止。也可点击下载官方兼容版本 v4.18.38，选择 Shell 或 Framework；安装器校验 GitHub 发布摘要、限制解压路径并保留原始 ZIP，安装成功仅填入目录，不自动启动 QQ。首次启动将发布包复制到应用私有运行目录，使用独立的本机端口与令牌，不改外部目录中的配置。应用不裁剪或修改 NapCat，使用与分发仍适用 NapCat 自身许可证。

启动后二维码仅在登录面板显示；登录成功后输入预期 QQ 号并点击接入收藏，应用内部配置本机接口，不需要手工复制令牌。关闭面板不会停止后台；显式停止或退出应用会清理本工具拥有的后台进程树。现有外部 QQ/NapCat 不会被接管或按名称批量终止。同账号桌面 QQ 可能阻止测试实例登录，此时需自行退出桌面 QQ。

退出时先取消排队项目，最多等待 60 秒让当前操作及回查结束。超时或存在未核验结果时，在应用数据目录保留 `qq-interrupted-task.json`；重新操作前应先刷新 QQ 收藏并核对，不能将中断视为失败后盲目重传。停止自有实例只清除属于它的连接令牌，手工配置的外部连接不受影响。

### QQ 收藏下载到本地

先刷新列表，再选择「下载已读取收藏」或跨页选择后下载所选。确认账号与数量后，后台逐项校验图片 MD5、格式和导入限制，复用本地库去重并生成缩略图；任务显示已导入、已存在、失败及取消。切换工作区后继续运行，完成后刷新本地列表。这里的全部指当前已拉取快照，不能保证 QQ 服务端未截断列表。

批次项目间保留可取消的 0.5 秒间隔；只读账号／收藏请求收到 HTTP 429 时最多重试两次，写请求绝不自动重传。容量与服务端分页没有已确认的公开接口，可使用 `scripts/qq_diagnostics.py` 对指定本机适配器做两轮只读数量及集合稳定性检查，结果不能单独证明完整性。

接口证据、诊断环境变量和各项真实／模拟验收范围见 [QQ 能力说明](docs/qq-capabilities.md)。

### S3 / WebDAV 云端库

设置云端同步的 endpoint、bucket、region、路径及访问凭据后，先测试连接，再上传本地库或下载云端库。S3 默认 Signature V4，旧服务兼容可显式选 V2；按服务要求选择 virtual 或 path 寻址。连接测试只读取配置前缀的一项列表，不验证写入和删除权限。WebDAV 同样提供只读连接检查。开启云端直接浏览后，清单与缩略图显示在本地网格，点击云端图片会校验下载入库。更换服务地址、存储桶或前缀时清除旧库缓存。

### Windows 构建与发行

仓库根目录 `.github/workflows` 提供检查、裁剪 ffmpeg 构建及 Windows 包构建工作流；`app/.github` 中是保留的上游文件。Windows 构建生成安装器和可解压运行的 portable ZIP，用户数据仍存于应用数据目录。构建需要 PyInstaller、MSVC/CMake、Inno Setup 和裁剪 ffmpeg，缺必需组件即失败。手动发布工作流默认只构建；正式发布需指定版本并显式启用发布。应用更新来源为本 fork `summer-foam/OhMyMeme`。
