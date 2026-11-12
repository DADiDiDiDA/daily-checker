# PROJECT · 项目地图（给 AI / 开发者）

> 本文面向「需要接着改这份代码」的人或 AI。读完应该能回答：代码在哪、数据怎么流、哪些约定不能破、改完怎么验证。
> 人类用户请看 [README.md](README.md)，版本变更请看 [update.md](update.md)。

**30 秒版**：`core.py` 是不带 Qt 的纯逻辑引擎（规则 / 扫描 / 状态 / 通知），`app.py` 是窗口 + 后台线程 + 单实例入口，`pages.py` 是各个页面，`widgets.py` / `theme.py` 是共用控件与主题。所有业务判定集中在 `core.py`，**改判定逻辑优先改 core，不要在 UI 里复制一份**。

---

## 1. 定位与边界

| 做什么 | 不做什么 |
| --- | --- |
| 扫描本机**自动化程序**写出的日志，判定每款游戏的「今日日常」是否完成 | 不读取游戏客户端日志，不联网查游戏账号数据 |
| 监控 `on_error` / `screencap` 异常目录、`debug` 日志体积、连续无错天数 | 不自动执行游戏 / 脚本，不修复异常 |
| 提供 GUI、托盘、定时检查、钉钉通知、命令行一次性检查 | 不更新任何程序（那是姊妹项目 [automate-updater](https://github.com/DADiDiDiDA/automate-updater) 的职责） |

同套 UI 框架的两个仓库（`daily-checker` / `automate-updater`）里 `app.py` 前 ~110 行、`theme.py`、`backdrop.py`、`widgets.py` 大量代码是同源的；在一个项目修了通用控件的坑，记得评估是否要同步到另一个。

## 2. 运行环境

- Windows 10/11（用了 `explorer`、`ctypes` 亚克力、注册表自启、系统托盘）
- Python 3 + `PySide6`；`pillow` 用于图标；`pyinstaller` 打包单文件 exe
- `core.py` **只依赖标准库**（`urllib` / `json` / `threading` / `dataclasses`…），不 import PySide6，可单独在纯 Python 环境里测试
- 子进程需静默：Windows 下用 `CREATE_NO_WINDOW`

## 3. 文件地图

| 文件 | 层 | 职责 / 关键入口 |
| --- | --- | --- |
| `core.py` | 逻辑 | 常量与内置数据（20 款游戏 / 6 条规则 / 20 条映射）、配置读写与迁移、规则库 CRUD、扫描与判定 `Engine`、消息构建与钉钉发送 |
| `app.py` | 应用 | `CheckWorker` / `NotifyWorker` / `TestDingWorker` 后台线程；`MainWindow` 窗口、导航、托盘、单实例 IPC、命令行解析、`main()`、`_excepthook` |
| `pages.py` | 页面 | `DashboardView`（总览）、`GameListView` / `GamesPage`、`RulesView` / `GameRulesView` / `FolderMapView`（判定规则三子页）、`SettingsPage`，以及全部 `ThemedDialog` 弹窗 |
| `widgets.py` | 控件 | `NavButton` / `Card` / `Badge` / `Chip` / `GameRow` / `StatTile` / `Toast` / `ToggleSwitch` / `ThemedCombo` / `NumberStepper` / `WrapLabel` / `SuffixMultiSelect` 等 |
| `theme.py` | 主题 | 3 套皮肤 × 明暗共 6 套调色板；`THEME` 单例；`tint()` / `flat()` / `scoped()` 内联样式工具 |
| `backdrop.py` | 背景 | 玻璃模糊背景绘制 / 实时取景 |
| `tray.py` | 托盘 | 图标绘制（青绿渐变 + 白对勾 + 红点角标）、右键菜单、悬停提示 |
| `autostart.py` | 系统 | 开机自启注册表读写，值名 `DailyChecker`（**不要**改成 `AutomateUpdater`，会与姊妹项目互相覆盖） |
| `DailyChecker.spec` | 构建 | PyInstaller onefile 配置，`assets/` 打进包，`console=False` |
| `assets/` | 资源 | 图标 png / ico |

依赖方向（**单向，不要反向 import**）：

```
app.py  ──>  pages.py  ──>  widgets.py ──> theme.py
   │             │                └──> backdrop.py
   └──> core.py（纯逻辑，不认识任何 Qt 类）  <── pages.py 也直接调用
```

`app.py` 通过 `Signal` 把 worker 结果送回主线程；`pages.py` 通过行级 signal 把用户操作冒泡给 `MainWindow`（例如 `game_edit_requested`），页面之间不互相持有引用。

## 4. 核心数据模型

**`GameConfig`（`core.py`，静态定义）**：`name / folder / target / extension / success_message`。`folder` 一律是「自动化程序根目录」，不是日志子目录；旧配置填到 `logs` 层时由 `program_dir_of()` 自动上退一层。

**`Game`（`core.py`，运行时对象，dataclass）**：

| 字段 | 含义 |
| --- | --- |
| `key / name / folder / target / extension / success_message` | 定义部分 |
| `program_dir` | 实际用于扫描的目录 |
| `daily` | `done` / `failed` / `unknown` |
| `detail / files_scanned / rule` | 失败原因、扫描文件数、规则说明（tooltip） |
| `log_size / threshold_mb / suspicious` | `debug` 顶层 `.log` 体积、阈值、是否存疑 |
| `error_flags` | `on_error` / `screencap` 集合，`has_error` 是其布尔视图 |
| `streak / state / scanned / excluded` | 连续无错天数 / `idle·running·done` / 本轮是否已检查 / 用户排除 |

**规则 dict**：`{id, name, methods[], keyword, file_target, ext, note}`。`methods` 取值见 `METHODS`（`keyword` / `keyword_today` / `log_today` / `file_exists` / `file_today`），任一命中即完成；`ext` 为空 = 不过滤后缀。

**`manual_states`**：`{游戏名: {"state": "done|failed|forced", "date": "YYYY-MM-DD"}}`；`fixed_marks` 是「已修复」的独立日期标记。**两者都只当天有效**。

## 5. 配置结构（`config/config.json`）

顶层四段，加载由 `load_config()` 完成「内置默认值 → 浅合并用户文件 → `_migrate_merge_m9a()` 迁移」：

| 段 | 关键键 | 说明 |
| --- | --- | --- |
| `ui` | `preset`(acrylic/monet/miuix) · `theme`(auto/light/dark) · `blur` · `geometry` · `settings_tab` | 外观与窗口位置 |
| `background` | `close_to_tray` · `check_enabled` · `interval_min` · `auto_notify` | 后台行为 |
| `dingtalk` | `enabled` · `webhook` · `secret` · `channel` | **默认全空**，凭证绝不入库 |
| `check` | 见下 | 全部检查业务配置 |

`check` 段重点键：`threshold_mb`(默认 10) · `thresholds`(单游戏覆盖) · `log_threshold_check`(总开关) · `threshold_enabled`(分项开关) · `cache_dir`(数据目录覆盖，空 = 程序根目录) · `excluded` · `rules`(`None` = 用内置规则库) · `rule_map`(游戏→规则 id) · `default_rule` · `custom_games` · `methods` · `log_folders` · `folder_map` / `folder_map_removed` · `fixed_marks` · `manual_states` · `summary_sort` · `sort_mode` / `sort_special` / `manual_order` · `rules_sort` / `rules_unbound_first` · `scan_dirs` / `scan_excludes` / `scan_depth`。

**路径约定**（容易踩）：
- `config_dir()` / `default_cache_root()` → 程序根目录；`cache_dir()` → `程序根目录/config`（**名字叫 cache，实际放 `config.json`**）。
- `config_path()` = `config/config.json`；`error_detect_state.json` 与 `DailyCheck.txt` 在**程序根目录**（由 `Engine._cache_root()` 决定，可被 `check.cache_dir` 覆盖）。
- 旧版默认数据目录 `D:\Automate\Cache` 由 `migrate_cache_state()` 一次性迁移，条件是「未手动设目录 + 新位置无文件 + 旧位置有文件」。
- 状态文件原子写：先写 `.tmp` + `fsync` 再 `os.replace`；配置损坏时先备份 `.corrupt`。

## 6. 主流程

**一轮检查**（`MainWindow` → `CheckWorker` → `core.Engine.run_check`）：

1. `expire_marks(cfg)` 清掉昨天的记号 → `collect()` 组装 20 款 + 自定义游戏为 `Game` 列表。
2. 每个游戏：`rule_id_for(cfg, name)` 取规则 → `_candidate_folders()` 定位日志目录（`logs/log/debug/.log` 顶层）→ `_files_of()` 按后缀过滤 → `_match_method()` 按规则里的方式判定 → 写 `daily / detail / files_scanned`。
3. `scan_errors()` 遍历 `scan_dirs`（深度限制 + `excludes` 只写文件夹名）找 `on_error` / `screencap`，用 `resolve_folder_name()` 做「包含即命中、取最长」映射回游戏名。
4. `update_streaks()` 按自然日更新连续无错天数；`save_error_state()` / `save_daily_log()` 落盘。
5. `build_all_messages()` 生成四条通知文本，按设置分发；结果通过 signal 回主线程，`DashboardView.refresh_stats()` 与 `GameListView` 重绘。
6. 排序走 `sort_items(items, mode, manual_order, cfg)`（`cfg` 必须传，否则手动状态不生效）。

**单款重扫**：UI 改了某款游戏的规则 / 阈值 / 目录时，只重扫这一款并刷新对应行，不要整轮重来。

**规则解析优先级**（`rule_id_for`，**别改顺序**）：
`rule_map[name]` 非空且规则存在 → `default_rule` → 旧版 `methods` 推断 → 内置 `DEFAULT_RULE_MAP`。
`is_game_bound()` 只认「有效显式绑定」；`set_game_rule(name, "")` = 删除绑定（回到跟随默认规则）。

## 7. 判定语义（唯一口径，勿在 UI 里另写一份）

| 函数 | 语义 |
| --- | --- |
| `effective_failed(cfg, i)` | **唯一**的「未完成」判定：`failed` 手动状态 → 算未完成；`done` / `forced` → 永不算；否则看 `daily == "failed"`。排序、筛选、计数全部走它 |
| `home_attention_visible(cfg, i)` | 是否参与首页体系（统计卡片 / 标题 / 关注列表 / 托盘角标 / 侧栏数字）：`excluded` 或 `forced` 完全不参与；`done` 仍参与（异常照提醒） |
| `expire_marks(cfg)` | 启动时与每轮检查开始时调用，按日期清 `fixed_marks` + `manual_states`，返回是否有变化 |
| `rule_usage()` | 还有游戏在用的规则不允许删除 |

**默认规则只作用于「未设定」的游戏**；显式选「未设定」、绑定被删留下悬空 id，都跟随默认规则。程序检查规则页对未设定行显示「未设定（用默认规则：X）」且**不预选默认规则**（否则 `changed` 不触发，游戏永远留在置顶区）；手动选任何规则（含默认规则）都算已设定并取消置顶。

## 8. 关键不变量与已知坑

改代码前请默认这些约束成立；破坏它们会造成难以复现的 UI / 判定 bug。

**Qt / 控件**
- **Qt 槽里的异常会被吞**：`app.py` 装了 `_excepthook` 写 `crash.log`，并按「异常类型 + 位置」60 秒合并计数。UI 改动后务必看一眼 `crash.log`，界面「看起来正常」不代表没抛异常。
- **无父级 `show()` / `setVisible(True)` 会弹出顶层小窗**（历史上导致启动疯狂闪烁）。控件先加入布局再显示；重建列表用 `hide()` + `removeWidget()` + `deleteLater()`，**不要 `setParent(None)`**。
- **无选择器的 `setStyleSheet` 会级联进 `QTipLabel` / 子按钮**（历史上 tooltip 变黑底灰字）。内联样式一律走 `theme.scoped()` / `tint()` / `flat()`；容器透明写 `QScrollArea > QWidget {…}` 这类带选择器规则。
- 折行文字用 `widgets.WrapLabel`（同步 `heightForWidth` 的最小高度）；`setFixedHeight` 太小 + 垂直 padding 大会裁掉按钮文字。
- `QLineEdit.end()` 在 PySide6 需要显式 bool 参数，按数字键可能抛异常 → 用 `setCursorPosition(len(text))`。
- `NumberStepper`：透明 `QLineEdit` 占满数值区，`_layout_editor` 由 `textChanged` 驱动，**单位由 `paintEvent` 紧跟文字绘制**并画 accent 高亮框；不要把单位画进 editor 背景。
- **别拿模块名当局部变量**：`except Exception as re` 会让整个函数里的 `re.sub(...)` 抛 `UnboundLocalError`（姊妹项目 v1.6.1 曾因此让所有 Release 更新全废）。可用 AST 静态检查守卫。

**测试与截图**
- `ThemedCombo.set_value()` / `ToggleSwitch.setChecked()` 测试时要传 `emit=True`；离屏 `exec()` 要打桩。
- 父窗口不 `show()` 时，子控件 `isVisible()` 恒为 `False`，别用它断言。
- **任何 UI 改动都要离屏完整构建一次 `MainWindow` 冒烟**，并把 `win._save_cfg` 打成 no-op，否则测试会写进真实配置。
- 截图前把 `core.APP_ID` 改成带 `.shot` 的值避免撞单实例；先 `THEME.set_mode("light")` + `refresh()` 再构造窗口（否则浅底配深字像没渲染）；离屏中文变方块时改用真实 `windows` 平台。

**业务与数据**
- 单实例 IPC 消息为 `{show, hidden, cli}`，二次启动的参数必须转交给已运行实例；退出统一走 `_shutdown()`，先取消并等待所有 worker。
- 开机自启值名固定 `DailyChecker`；默认配置**不得**内置任何真实钉钉凭证。
- 规则页排序（`check.rules_sort` + `rules_unbound_first`）由 `MainWindow` 注入 `scan_items_provider=lambda: self.games`；改排序只调行顺序（`removeWidget` / `insertWidget`），**不重建行控件**。
- 规则库内置条目不允许删除（在用时）；`default_rule` 同时只有一条。
- `sort_items` 的 `cfg` 参数漏传是最常见的「排序不尊重手动状态」原因。

## 9. 构建与验证

```powershell
python -m PyInstaller DailyChecker.spec --noconfirm --clean
python app.py --check            # 命令行跑一轮，不发通知
```

- 打包产物在 `dist/`；`.gitignore` 排除 `config/`、`_test/`、`build/`、`dist/`、`*.log`、状态文件与开发截图。
- 验证脚本放在 `_test/`（不入库），惯例按版本命名 `verify_dc_v<版本>.py`；核心逻辑改动后优先补纯 Python 断言，UI 改动再补离屏冒烟。
- 提交前确认：`config/config.json` 没被带上、没有真实凭证、版本号与 `core.APP_VERSION` 一致。

## 10. 改动自查清单

1. 判定 / 排序逻辑是否只在 `core.py` 改了一处，且所有调用方都传了 `cfg`？
2. 新增控件是否父级就绪后才 `show()`、内联样式是否带作用域？
3. 有没有在 Qt 槽里新增可能抛异常的代码？`crash.log` 是否干净？
4. 配置新增键是否同时补进 `load_config()` 的默认值，旧配置能否浅合并兼容？
5. 首页统计、关注列表、托盘角标、侧栏数字是否仍统一走 `home_attention_visible()` / `effective_failed()`？
6. 离屏构建 `MainWindow` 冒烟通过了吗？`_save_cfg` 打桩了吗？
