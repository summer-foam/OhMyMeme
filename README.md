# QQ 收藏表情链路验证

基于 OhMyMeme 的应用源码位于 `app/`，来源为 dev 分支 `ee7e38b8028954a6454f03df2f7e0ba7b8f88fe7`，保留原始 GPL-3.0 许可与署名。QQ 收藏功能的使用及开发说明见 `app/README.md`、`app/CONTRIBUTING.md`。本目录保留独立链路验证程序及被 Git 忽略的本机验证产物。

当前已接入 OhMyMeme UI，支持 Shell / Framework 管理、QQ 收藏浏览、批量上传与删除，以及滑动工作区和逐显示器 DPI 感知。`qq_probe.py` 保留为 Python 3.12 标准库链路验证程序。

## 早期链路验证记录（2026-10-20）

- QQ NT 安装版本：9.9.36-53644。
- 测试账号：944 张原图，JPG 569、PNG 65、GIF 291、WebP 19；读取错误 0，SHA-256 完全重复 0。
- 一张本地原图已复制至 `artifacts/export-sample/`，MD5/SHA-256 一致。
- 5 项单元测试通过；这不代表真实 QQ 回写通过。
- NapCat v4.18.38 Shell 发布包已下载到临时目录，SHA-256 与 GitHub 发布摘要一致；非管理员加载器启动成功，退出原桌面 QQ 后快速登录成功。
- 真实列表读取：请求 48 返回 48，请求 2000 和 5000 均返回 980，980 个不同 MD5。这提供当前列表稳定的证据，尚未与 QQ UI 显示总数交叉核对，不能保证其他账号及版本的上限或分页行为。
- 云端清单与原本地快照按 MD5 对比：云端缺少本地文件的记录 92，本地不在云端清单中的文件 56。不能把两者数量差 36 当作实际缺失数量。
- 云端缺失文件抽样下载 2,253,914 字节，下载后 MD5 与收藏记录一致。
- 真实单张添加通过：接口 result=0、isExist=0，回查 MD5 命中，收藏数 980 → 981。
- 同一图片重复添加返回 result=0、isExist=1，收藏数保持 981。
- 只删除本次新建测试资源成功，回查测试 MD5 不存在，收藏数恢复 980。结果位于 `artifacts/live-upload-result.json`。
- 本次仅证明读取、抽样下载、单张添加、重复添加及测试资源删除；批量上传、限额、限流、跨端显示、描述/标签/排序同步未验证。

## 本地读取与抽样导出

```powershell
python qq_probe.py
```

读取 QQ 的 UserDataInfo.ini，扫描各账号 `personal_emoji/Ori`，按文件头识别格式，计算 MD5/SHA-256，并复制一张图片核对哈希。源目录只读。输出在被 Git 忽略的 `artifacts/`，其中可能包含账号、图片路径及下载地址，请勿公开分享。相同输出位置的样本已存在时拒绝覆盖；重复验证可用 `--out artifacts/run2/inventory.json`。

缓存清单不等于当前完整云端收藏列表。

## NapCat 读取与单张回写

需要已登录的本机 NapCat，启用仅监听 `127.0.0.1` 的 OneBot HTTP 服务。令牌通过环境变量 `NAPCAT_TOKEN` 提供，不写入脚本或命令行参数。

```powershell
python qq_probe.py --endpoint http://127.0.0.1:3000 --out artifacts/qq-favorites.json
python qq_probe.py --endpoint http://127.0.0.1:3000 --account <预期QQ号> --upload <本地测试图片路径> --out artifacts/upload-result.json
```

回写先核对账号，再按 MD5 跳过已存在图片。添加后轮询收藏详情，查到相同 MD5 才标记 `verified`；否则保留 `submitted_unconfirmed`，不盲目重传。未实现删除、排序或标签同步。

`--count` 默认请求 2000 条，但返回条数和接口上限尚未实测，因此报告始终标记列表完整性未验证。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## OhMyMeme 接入方向

已审阅上游 dev 分支 `ee7e38b8028954a6454f03df2f7e0ba7b8f88fe7`：Python + pywebview/Bottle + SQLite，主界面为 Vue 3/Vite 6；已有 QQ 缓存导入模块和插件系统。

建议先通过插件的主窗口 API/按钮扩展完成“选中图片 → QQ 收藏差异预览 → 添加队列 → 结果核对”，复用现有库、图片路径和选择状态。UI 重设计再在 fork 中修改 Vue 组件及主题。上游是 GPL-3.0，复用及分发需保留其许可与署名。

参考：

- https://github.com/OhMyMeme/OhMyMeme
- https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/extends/CustomFace.ts
- https://napneko.github.io/guide/boot/Shell
