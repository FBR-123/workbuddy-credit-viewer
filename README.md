# WorkBuddy 积分挂件

**简体中文** · [English](README.en.md)

> 一个常驻桌面的极小胶囊，把 WorkBuddy 的**多账号积分余额、今日消耗、到期风险**摊在屏幕一角。
> 随客户端启动而出现，随客户端关闭而消失。**零第三方依赖，单文件，纯 Python 标准库。**

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Core](https://img.shields.io/badge/core-1%20file%20~2.8k%20lines-blue)
![License](https://img.shields.io/badge/license-MIT-green)

![胶囊与积分预览面板](assets/01-capsule-and-preview.png)

---

## 这东西解决什么问题

WorkBuddy 的积分**按月清零、不结转**。多账号一起用的时候，会踩到三个很具体的坑：

| 痛点 | 后果 |
|---|---|
| 积分藏在客户端深处，看不到余额 | 月底突然发现一大笔额度已经清零 |
| 一个客户端同时只能登录一个账号 | 另外几个号的余额和到期时间完全不可见 |
| 不知道按当前消耗速度还能用几天 | 要么不敢用，要么某天突然不够用 |

这个挂件就是为这三件事做的：**把 多个账号的余额、今日消耗、最近到期时间，压进一行 800px 的胶囊里常驻显示。**

---

## 特性

| | |
|---|---|
| **多账号汇总** | 自动扫描本机所有历史登录态，按 uid 去重，一次看全 |
| **三层刷新** | 15 秒刷全部账号余额 / 60 秒完整刷当前账号 / 600 秒全量盘点，消耗 15 秒内可见 |
| **多账号轮播** | 胶囊每 10 秒轮换一个号（`⟳ 3/5`），不用点也能看到别的号 |
| **到期标红** | 3 天内有额度清零 → 边框变淡红 + `⚠` 闪烁，并弹提醒（3d / 24h / 6h 三级去重） |
| **耗尽预测** | 按近 7 天实际余额差算日均消耗，直接告诉你「≈耗尽约 6 天 7 时」 |
| **变化提示** | 「今日」后面闪一下红色 `+N`，让你知道刚刚那笔消耗被数到了 |
| **随客户端启停** | 登录后只起一个无界面监听器，客户端一开才出现窗口，一关就退出 |
| **零依赖** | 只要 Python + tkinter，不装任何包，不怕环境哪天坏掉 |

---

## 截图

> 全部截图使用**合成数据**演示，不含任何真实账号、余额或到期时间。

单击胶囊 → 积分预览面板（全部账号 + 近 5 天逐笔到期）：

![积分预览面板](assets/01-capsule-and-preview.png)

积分快用完时，按消耗速度预测并提醒：

![耗尽提醒](assets/02-runout-alert.png)

到期前 24 小时 / 3 天，把即将清零的批次逐笔点名：

![到期提醒](assets/03-expiry-alert.png)

客户端启动时自动弹一次多账号盘点：

![启动盘点](assets/04-startup-summary.png)

---

## 快速开始

### 0. 拿到代码

```bat
git clone https://github.com/FBR-123/workbuddy-credit-viewer
cd workbuddy-credit-viewer
```

（不装 Git 就点页面右上角 **Code → Download ZIP** 解压。）

### 环境要求

- **Windows 10 / 11**
- **Python 3.9+**，且**必须带 tkinter**（官网安装包默认自带）
- WorkBuddy 桌面客户端，且**至少登录过一个账号**

> [!IMPORTANT]
> 请用**系统安装的 Python**，不要用某些工具自带的精简 Python——它们通常**不含 tkinter**，挂件会直接起不来。
> 自查：`python -c "import tkinter"` 不报错就行。

### 1. 不用改任何路径

启动器会自己找 Python，按这个顺序：

1. 环境变量 `WB_PYTHONW`（指到 `pythonw.exe`）
2. `%LOCALAPPDATA%\Programs\Python\*\pythonw.exe`（按用户安装，最常见）
3. `%ProgramFiles%\*\pythonw.exe`、`%ProgramFiles%\Python\*\pythonw.exe`（装给所有用户）
4. 以上都没有 → 弹窗提示，这时手动设一下：

```bat
:: 把下面这条换成 `where pythonw` 查到的路径
setx WB_PYTHONW "C:\Users\你\AppData\Local\Programs\Python\Python313\pythonw.exe"
```

> 只有装在非常规位置时才需要这一步。装在默认位置的直接跳到下一步。

### 2. 先手动跑一次

```bat
start_widget.vbs
```

胶囊会出现在屏幕右上角，边框闪 3.5 秒。**首帧数据大概要等 5~15 秒**（要逐个账号调接口）。

右键胶囊 → **复位到右下角**，可以把位置拉回来。

### 3. 设置成「随 WorkBuddy 自动启动」

```bat
autostart_on.vbs
```

或者右键胶囊 → **随 WorkBuddy 自动启动** → 启用。

之后：**登录电脑时什么窗口都不会出现**，只有打开 WorkBuddy 客户端，胶囊才冒出来。

不想要任何后台程序了：

```bat
autostart_off.vbs
```

### 4. 查状态 / 排错

```bat
python wb_credit_widget.py --version            :: 版本号
python wb_credit_widget.py --autostart status   :: 自启项 / 监听器 / 挂件 / 客户端 四项状态
python wb_credit_widget.py --dump               :: 命令行打印一次汇总，不启动界面
python wb_credit_widget.py --stop-all           :: 同时停掉挂件和监听器
python tests\diagnose_dead.py                   :: 现场诊断
```

---

## 使用说明

### 胶囊怎么读

```
● ⟳ 138****8888 3/5 │ 余额 5,666 │ 今日 0 │ 3日到期 322 │ 5日 542  ≈耗尽约6天7时  8s前
```

| 区块 | 含义 |
|---|---|
| `●` | 数据状态点 |
| `⟳ 账号 N/M` | 正在轮播，当前是第 N 个号（关闭轮播时不显示） |
| `余额` | 当轮账号余额 |
| `今日` | **全部账号**今日消耗合计；有变化时后面闪红 `+N` |
| `3日到期` | 3 天内会清零的额度；`>0` 时整条边框变淡红、数值标红、`⚠` 闪烁 |
| `≈耗尽约X天Y时` | 按近期消耗速度预测的耗尽时间，只在低于阈值时出现 |
| `8s前` | 上次同步距今多久（`同步中…` / `已同步` / `Ns前`） |

### 交互

| 操作 | 效果 |
|---|---|
| **单击** | 弹出积分预览面板（全部账号 + 近 5 天逐笔到期清单） |
| **拖动** | 移动位置，松手记忆 |
| **右键** | 刷新 / 视图 / 轮播开关与间隔 / 各档刷新间隔 / 置顶 / 圆角 / 复位 / 随客户端自启 / 提醒阈值 / 退出 |

### 常用配置

改 `%LOCALAPPDATA%\WBCreditWidget\config.json`，或直接在右键菜单里调：

| 键 | 默认 | 说明 |
|---|---|---|
| `poll_seconds` | 15 | 全部账号只刷余额的间隔 |
| `refresh_seconds` | 60 | 完整刷新当前账号的间隔 |
| `full_seconds` | 600 | 全账号盘点间隔 |
| `rotate_seconds` | 10 | 轮播间隔，`0` = 关闭 |
| `follow_workbuddy` | `true` | 客户端不在时隐藏界面 |
| `exit_with_workbuddy` | `true` | 客户端关闭时自己也退出（仅监听器拉起的实例） |
| `runout_days` | 7 | 预测剩余 ≤ N 天时提醒，`0` = 关闭 |
| `runout_min_balance` | 100 | 单账号低余额提醒阈值 |
| `alert_days` | 3 | 到期标红 / 提醒的天数阈值 |

### 数据与隐私

- 积分数据来自本机登录态，**cookie / token 不落盘、不上传、不打印**；
- 界面和日志里账号一律脱敏为 `138****8888`；
- 本地只存三类东西：`config.json`（配置）、`state.json`（当日基线 + 消耗采样 + 提醒去重）、`*.log`（日志）；
- 全部在 `%LOCALAPPDATA%\WBCreditWidget\` 下，删掉即清空。

---

## 工作原理

```
登录
 └─► 启动项 WorkBuddyCreditWidget.vbs（纯 ASCII，延迟 6 秒）
        └─► pythonw wb_credit_widget.py --watch      ← 无窗口、无网络、只轮询
               ├─ 客户端窗口出现 ──► Popen --managed ──► 胶囊出现
               └─ 客户端窗口消失 ──► 宽限 15s 后结束自己拉起的子进程
```

两个关键设计，都是踩了坑才定下来的：

**1. 判断客户端在不在，看「窗口」不看「进程」。**
客户端关掉窗口后，`daemon` / `sidecar` / `codebuddy --prewarm` 池 / `--gc_retain_sec 600` 的 job 容器
还会活 10 分钟，**映像名同样是 `WorkBuddy.exe`**。只看进程会一直以为「客户端还开着」，挂件就永远关不掉。
所以判据是 `EnumWindows` + `IsWindowVisible`（顺带：最小化不算关闭——Electron 最小化不隐藏窗口）。

**2. 单实例检测用「进程身份」，不是「pid 还活着」。**
硬关机 / 重启时 pid 文件来不及清理，那个 pid 会被别的程序（实测是 `svchost.exe`）复用。
只判 `pid 存活` 会得到假阳性 → 新实例以为自己已经在跑 → 静默退出 → **表现为「有时开机没窗口」**。
所以 pid 文件写两行：`pid` + **进程创建时间**，读的时候校身份。

更多实现细节（含三层刷新算法、耗尽预测、每一处设计取舍）见 [docs/详细使用手册.md](docs/详细使用手册.md)。

---

## 已知限制

| 限制 | 说明 |
|---|---|
| **仅 Windows** | 大量依赖 `ctypes` 调 Win32（进程、窗口、GDI） |
| **需要客户端登录态** | 不登录就用不了；这是设计如此，不是缺陷 |
| **接口是逆向的** | 走客户端内部 billing 接口，**官方随时可能改**，改了就得跟着修 |
| **部分账号可能 401** | token 过期后该账号显示「取数失败：HTTP 401」，需要**在客户端重新登录该账号一次** |
| **非官方工具** | 与 WorkBuddy / CodeBuddy 官方无关，见下方免责声明 |

---

## 常见问题

**挂件不见了？**
1. 先确认 WorkBuddy 客户端在运行（默认跟着它来去）；
2. 眼睛找不到就盲点屏幕右上角；
3. `python wb_credit_widget.py --autostart status` 看是哪一环没跑；
4. 想立刻要：双击 `start_widget.vbs`。

**关掉客户端了，挂件还在？**
看 `%LOCALAPPDATA%\WBCreditWidget\widget.log`：
- 有 `quitting: WorkBuddy window closed` → 它退过，你看到的是新拉起的；
- 只有 `widget started` → 这个实例是**手动双击启动**的，不受托管约束（设计如此），右键退出即可。

**数字不动？**
1. `python wb_credit_widget.py --dump` 看实时值；若和胶囊一致 → 你确实没消耗；
2. 开着轮播时，`余额` 显示的是**当轮那个号**，会每 10 秒变一次；
3. 想立刻刷新：右键 → 立即刷新。

**某个账号显示「取数失败 HTTP 401」？**
该账号登录态过期了，去客户端重新登录一次即可。

**首日看不到「耗尽预测」？**
预测需要跨天样本。只跑了不到一天的，面板会标「仅按今日推算」，不主动提醒——这是**故意的**，否则首日必然误报。

---

## 项目结构

```
credit_widget/
├── wb_credit_widget.py     # 主程序：UI / 取数 / 轮询 / 监听器 / 自启 / 进程身份校验
├── shot_widget.py          # 截图自检工具（纯 ctypes + zlib 手写 PNG，不依赖 Pillow）
├── _findpy.bat             # Python 发现逻辑（要求带 tkinter），所有启动器共用
├── start_widget.bat/.vbs   # 手动启动（无黑窗口）
├── watch_widget.bat        # 只跑监听器
├── stop_widget.bat         # 停止挂件 + 监听器
├── autostart_on.bat/.vbs   # 装「随客户端自动启动」
├── autostart_off.bat/.vbs  # 卸掉
├── assets/                 # 截图
├── docs/
│   ├── 详细使用手册.md      # 完整说明（每个功能的设计取舍）
│   └── 发布文案.md          # 仓库简介 / 社交平台短文案
├── tests/                  # 8 个测试 + 3 个静态检查 + 1 个诊断脚本
└── .github/workflows/ci.yml
```

> `.vbs` 里**故意一行逻辑都没有**，只负责把对应的 `.bat` 隐藏窗口跑起来。
> 因为有些机器上脚本宿主被安全策略直接拦死，`.bat` 才是能真正执行、能被测试验证的那一层。

---

## 测试

```bat
tests\run_tests.bat
```

必须用带 tkinter 的 Python（`run_tests.bat` 会自动找，找不到会给出提示）。

| 测试 | 断言 | 覆盖 |
|---|---:|---|
| `test_window_detection.py` | 12 | 窗口判据：残留进程没窗口 → 必须判「已关闭」；**最小化不算关闭** |
| `test_watch_logic.py` | 21 | 监听器每帧决策、cooldown、闪断不误杀、自启项原子写入 |
| `test_pid_identity.py` | 18 | pid 身份校验：指向别的程序 / 创建时间不符 → 判过期并清理 |
| `test_lifecycle.py` | 6 | `--managed` 实例优雅退出（有 marker 为证） |
| `test_menu_smoke.py` | 6 | 43 项菜单能构建、动作方法不抛异常 |
| `test_pipeline_e2e.py` | 13 | 全链路：出现→拉起→消失→退出→再拉起 |
| `test_stale_pid_recovery.py` | 6 | 复现「脏 pid 文件」，三种形态都必须能正常启动 |
| `test_real_window_flow.py` | 13 | 真实窗口全流程：销毁窗口但进程活着 → 必须退出 |
| `check_autostart_vbs.py` | 8 | 启动项纯 ASCII / 无 BOM / 路径存在 / 带 `--watch` |
| `check_vbs_syntax.py` | 27 | 所有 `.vbs`：块结构、`Option Explicit` 变量声明、引用文件存在、**且不允许有控制流**（逻辑必须在 `.bat` 里） |
| `check_no_secrets.py` | — | 提交前体检：凭证 / 完整手机号 / 邮箱 / 硬编码绝对路径 |
| `diagnose_dead.py` | — | 现场诊断（非断言）：pid 文件 vs 真实映像名、日志、开机时刻 |

所有测试都用**隔离的临时数据目录**，不会碰你真实的 `config.json` / `state.json`。

---

## 安全说明

- **不存储任何凭证**：token 从本机登录态现读现用，不落盘、不上传、不打印；
- **账号显示一律脱敏**（`138****8888`），`state.json` / 日志里也一样；
- **唯一的第三方代码入口**是「签到技能」里的 `workbuddy_checkin`（用来解 token 的加密信封）。
  它只在 `~/.workbuddy/skills/*checkin*/scripts/` 下查找，**不会**去导入其它技能目录里的同名文件
  （否则等于给本机所有第三方技能一个在启动时执行代码的机会）。
  装在别处就设 `WB_CHECKIN_SKILL_DIR`；不装也能用，只是解不了新版本的加密信封。
- `--stop-all` / `--stop` 杀进程前会校验 pid 的**进程身份**（映像名 + 创建时间），
  不会因为 pid 被复用而误杀无关程序。

---

## 免责声明

- 本项目是**个人自用工具**，与 WorkBuddy / CodeBuddy 官方**没有任何关系**，非官方产品；
- 积分数据来自本机登录态对官方接口的读取，**数据归属官方**；本项目不存储、不上传、不分发任何凭证；
- 内部接口**没有稳定性承诺**，官方改版后可能失效；
- 请仅用于**查看自己账号的积分**，不要用于批量、高频或任何违反服务条款的用途；
- 使用风险自负。

---

## License

[MIT](LICENSE)
