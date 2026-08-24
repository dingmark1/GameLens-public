# GameLens 项目结构说明

## 1. 项目概览

GameLens 是一个基于 Python + PyQt6 的游戏文本识别、翻译与资料管理工具。当前代码主要围绕“屏幕框选 -> OCR 识别 -> DeepSeek 翻译 -> 本地持久化 -> 辅助维护”这条链路展开，并补充了游戏简介生成、对话记忆、人物信息维护等功能。

整体上可以拆成四层：

1. **启动与界面层**：负责程序入口、主窗口、管理窗口、编辑对话框与框选交互；
2. **识别与生成层**：负责 OCR 识别、文本翻译、游戏简介生成；
3. **记忆与数据库层**：负责对话记忆、摘要生成、SQLite 持久化；
4. **配置与资源层**：负责读取运行配置、加载 Qt 界面文件、保存本地数据库。

---

## 2. 目录与文件说明

### 2.1 `src/main.py`

程序入口。

**作用**
- 创建 `QApplication`
- 启动前清空进程内的框选区域缓存
- 创建并显示主窗口 `MainWindow`
- 进入 Qt 事件循环

**关键功能**
- `main()`：应用启动流程

**说明**
- 这里是整个程序的启动起点，所有 UI、识别、翻译与记忆模块都从这里间接进入运行状态。

---

### 2.2 `src/core/app_config.py`

应用配置加载模块。

**作用**
- 从 `config/config.txt` 读取配置
- 将配置解析为类型安全的 `AppConfig`
- 向全局暴露常用配置值

**关键功能**
- `get_app_config()`：读取并缓存配置
- `_parse_bool()`：解析布尔配置
- `_load_config()`：加载配置文件
- `_parse_config()`：组装配置对象

**配置项**
- `deepseek_api_key`
- `enable_ocr_preprocess`
- `top_proximity_threshold`
- `memory_window_size`
- `auto_recognition_interval_ms`

**说明**
- 该模块把配置读取与业务代码解耦，避免各模块直接解析文本配置。
- 配置会在模块初始化时形成常量导出，便于其他模块直接引用。

---

### 2.3 `src/core/ocr_engine.py`

OCR 识别封装模块。

**作用**
- 初始化并复用 `PaddleOCR`
- 处理截图预处理
- 从 OCR 结果中提取坐标与文本
- 组织成“人名 / 对话”结构化结果

**关键功能**
- `set_ocr_language()`：切换 OCR 语言
- `get_ocr_engine()`：获取单例 OCR 引擎
- `prewarm_ocr_engine()`：预热模型，减少首次识别延迟
- `recognize_texts()`：执行 OCR 并返回带坐标的文本块
- `format_dialog_result()`：将文本块整理成结构化对话结果

**辅助逻辑**
- 图像预处理：灰度化、双边滤波
- 大图切片识别
- 文本框坐标提取与排序
- 重叠识别结果合并

**说明**
- 该模块输出的是结构化 OCR 结果，而不是单纯字符串，便于后续翻译、记忆和数据库写入。

---

### 2.4 `src/core/translator.py`

DeepSeek 翻译模块。

**作用**
- 接收 OCR 结构化结果
- 拼装系统提示词与用户提示词
- 调用 DeepSeek Chat API
- 将返回结果规范化为统一结构

**关键功能**
- `translate_dialog_result()`：主翻译入口
- `has_translatable_content()`：判断是否存在可翻译内容
- `TranslationError`：翻译异常类型

**辅助功能**
- `_build_system_prompt()`：构造翻译提示词
- `_build_user_prompt()`：拼装请求数据
- `_call_deepseek_api()`：请求接口
- `_parse_json_content()`：解析 JSON 响应
- `_normalize_translation()`：整理返回结构

**说明**
- 这个模块强调“结构化输出”，翻译结果会尽量维持和 OCR 输入一致的字段组织，方便主窗口继续处理。

---

### 2.5 `src/core/game_intro_generator.py`

DeepSeek 游戏简介生成模块。

**作用**
- 构造游戏资料检索与简介撰写提示词
- 调用 DeepSeek Chat API
- 校验并返回模型生成的游戏简介

**关键功能**
- `generate_game_intro()`：根据游戏名生成简介
- `GameIntroGenerationError`：简介生成异常类型
- `_parse_json_content()`：解析返回的 JSON 内容

**说明**
- 该模块生成的是“游戏简介”而不是对话翻译，输入和输出目标都和 OCR 翻译链路独立。

---

### 2.6 `src/memory/database.py`

SQLite 持久化层。

**作用**
- 管理游戏、游戏简介、人物、对话、摘要数据
- 自动建表、建索引、做必要迁移
- 为 UI 与记忆模块提供统一数据接口

**核心数据表**
- `games`
- `characters`
- `dialogues`
- `summaries`
- `game_intros`

**关键功能**
- `add_game()` / `delete_game()` / `list_games()`
- `add_character()` / `update_character()` / `delete_character()`
- `add_dialogue()` / `update_dialogue()` / `delete_dialogue()` / `clear_dialogues()`
- `add_summary()` / `update_summary()` / `delete_summary()`
- `add_game_intro()` / `update_game_intro()` / `delete_game_intro()`
- `get_game_intro_by_game_name()` / `get_game_intro_by_game_id()`
- `get_all_game_intros_with_game_name()`
- `get_all_dialogues_with_game_name()`
- `get_all_characters_with_game_name()`
- `get_all_summaries_with_game_name()`
- `get_latest_summary()` / `get_latest_summary_record()`
- `get_dialogues_by_game_range()`
- `get_character_by_name_original()` / `character_exists()`

**额外能力**
- 对话表迁移
- 人物删除时级联处理对话引用
- 线程锁保护 SQLite 访问
- 触发器保证人物删除后对话中的角色引用自动置空

**说明**
- 该模块是数据中枢，上层所有“列表展示、编辑、删除、生成后写入”操作最终都会落到这里。

---

### 2.7 `src/memory/conversation_memory.py`

对话记忆与摘要生成模块。

**作用**
- 维护全局对话窗口记忆
- 去重 OCR 对话结果
- 自动触发前情回顾生成
- 基于最近对话推断人物补充信息
- 必要时从数据库重建最近记忆

**关键功能**
- `get_conversation_memory()`
- `append_conversation_record()`
- `append_ocr_dialog_result()`
- `get_recent_conversation_records()`
- `get_conversation_summary()` / `set_conversation_summary()`
- `clear_conversation_memory()` / `clear_conversation_summary()`
- `load_recent_conversation_records_from_database()`
- `is_duplicate_ocr_dialog_result()`

**摘要相关**
- `_trigger_summary_generation_if_needed()`
- `_trigger_temporary_summary_generation_if_needed()`
- `_generate_summary_task()`
- `_generate_temporary_summary_task()`
- `_call_summary_api()`
- `_build_summary_prompt()`

**人物信息相关**
- `_call_character_relation_inference_api()`
- `_build_character_relation_prompt()`
- `_parse_addition_updates()`
- `_merge_extra_info()`
- `_apply_summary_addition()`

**说明**
- 该模块同时处理“内存记忆”与“数据库回填”两条路径，保证程序重启后仍能恢复最近上下文。
- 它还负责把对话摘要和人物补充信息的自动推断串起来，减轻手工维护成本。

---

### 2.8 `src/ui/main_window.py`

主窗口与业务调度中心。

**作用**
- 承载主界面 UI
- 协调选区、OCR、翻译、存储、展示
- 管理自动识别与后台线程
- 打开人物 / 对话 / 游戏简介 / 摘要管理窗口
- 在经典模式与实验模式窗口之间切换

**关键类**
- `AddCharacterDialog`
- `OcrRecognitionWorker`
- `OcrPrewarmWorker`
- `MainWindow`

**主要能力**
- 框选屏幕区域
- 截图并执行 OCR
- 调用翻译接口
- 识别结果写入数据库与内存记忆
- 自动识别 / 单次识别切换
- 游戏列表增删改联动
- 清空对话记忆 / 摘要
- 在识别过程中辅助补全人物信息
- 一键切换到 `GameLens_beta`，并在切换时关闭主窗口相关管理窗口

**重要方法**
- `MainWindow._start_screen_region_selection()`
- `MainWindow._start_ocr_recognition()`
- `MainWindow._perform_single_recognition()`
- `MainWindow._start_auto_recognition()`
- `MainWindow._stop_auto_recognition()`
- `MainWindow._show_translation_overlay()`
- `MainWindow._process_pending_dialogue_storage()`

**说明**
- `AddCharacterDialog` 负责把识别过程中补充的人物信息写入数据库。
- `OcrRecognitionWorker` 把 OCR 和翻译等耗时工作放到后台线程，避免主线程卡顿。
- `MainWindow` 是整个 UI 的中枢，其他管理窗口都由它统一调度和打开。

---

### 2.9 `src/ui/screen_region_selector.py`

屏幕框选与翻译覆盖层模块。

**作用**
- 提供全屏透明框选界面
- 把逻辑坐标转换为真实显示器坐标
- 负责截图、模糊背景和翻译浮层显示
- 在进程内缓存选区

**关键类**
- `ScreenSelectionOverlay`
- `SelectionOutlineOverlay`
- `SelectionCancelButtonOverlay`
- `TranslationOverlay`

**关键功能**
- `save_selection_rect_to_memory()`
- `reset_selection_rect_memory()`
- `load_selection_rect_from_memory()`
- `capture_selection_with_mss()`

**辅助逻辑**
- 多显示器几何映射
- 高 DPI 坐标换算
- 选区截图
- 选区边框与取消按钮悬浮层

**说明**
- 目前框选区域只保存在内存中，程序重启后会丢失，避免把纯运行时状态写入配置文件。
- 该模块同时承担交互层和截图层职责，是主窗口发起识别前的第一步。

---

### 2.10 `src/ui/game_intro_window.py`

游戏简介窗口。

**作用**
- 展示当前游戏的简介
- 调用 DeepSeek 生成并保存游戏简介
- 修改或删除已有游戏简介

**关键类**
- `GameIntroGenerationWorker`
- `GameIntroWindow`

**主要能力**
- 按游戏名加载简介
- 后台生成游戏简介，避免阻塞 UI
- 重新生成时支持确认替换
- 生成结果落库后同步刷新界面
- 支持手工修改和删除

**说明**
- 这个窗口和对话翻译无关，专门服务于“游戏元信息”的维护。

---

### 2.11 `src/ui/character_manager_window.py`

人物管理窗口。

**作用**
- 展示人物列表
- 修改人物译名 / 性别 / 补充信息
- 删除人物记录

**关键功能**
- `refresh_characters()`
- `_on_edit_clicked()`
- `_on_delete_clicked()`
- `closeEvent()`

**说明**
- 该窗口直接面向数据库中的 `characters` 表，适合对识别结果做人工校正。

---

### 2.12 `src/ui/dialogue_manager_window.py`

对话管理窗口。

**作用**
- 展示所有对话记录
- 修改对话内容
- 删除单条对话
- 清空全部对话

**关键功能**
- `refresh_dialogues()`
- `_on_edit_clicked()`
- `_on_delete_clicked()`
- `_on_clear_clicked()`

**说明**
- 该窗口与 `dialogues` 表直接对应，是人工清理识别噪声和修正文案的重要入口。

---

### 2.13 `src/ui/summary_manager_window.py`

摘要管理窗口。

**作用**
- 展示摘要列表
- 修改摘要内容
- 删除摘要记录

**关键功能**
- `refresh_summaries()`
- `_on_edit_clicked()`
- `_on_delete_clicked()`

**说明**
- 该窗口面向 `summaries` 表，便于查看自动总结的前情回顾并进行修订。

---

### 2.14 `src/ui/edit_character_dialog.py`

人物编辑对话框。

**作用**
- 修改选中人物的可编辑字段
- 保存后通知列表刷新

**关键功能**
- `EditCharacterDialog._on_accept()`

**说明**
- 原文名和所属游戏是只读的，避免破坏人物和对话之间的关联关系。

---

### 2.15 `src/ui/edit_dialogue_dialog.py`

对话编辑对话框。

**作用**
- 修改选中对话内容
- 校验人物与游戏关系

**关键功能**
- `EditDialogueDialog._on_accept()`

**说明**
- 这个对话框会检查人物是否属于当前游戏，避免把对话编辑成无效关联。

---

### 2.16 `src/ui/edit_summary_dialog.py`

摘要编辑对话框。

**作用**
- 修改选中摘要内容
- 展示所属游戏和覆盖范围

**关键功能**
- `EditSummaryDialog._on_accept()`

**说明**
- 覆盖范围会在界面上明确展示，便于判断该摘要对应的是哪一段对话区间。

---

### 2.17 `src/core/__init__.py`

核心模块导出入口。

**作用**
- 统一导出 OCR、翻译、配置、游戏简介生成等常用对象/函数

**说明**
- 便于上层模块按包级别导入，减少零散依赖路径。

---

### 2.18 `src/memory/__init__.py`

记忆模块导出入口。

**作用**
- 暴露对话记忆、摘要、数据库能力给上层调用

**说明**
- 作为包初始化文件存在，同时承担常用能力聚合的角色。

---

### 2.19 `src/ui/__init__.py`

UI 模块导出入口。

**作用**
- UI 包初始化文件
- 便于模块化导入

---

### 2.20 `src/ui/main_window.ui`

Qt Designer 界面文件。

**作用**
- 定义主窗口布局、按钮、下拉框等控件
- 由 `MainWindow` 通过 `uic.loadUi()` 加载

**说明**
- 界面结构与业务逻辑分离，便于后续调整布局而不必重写主窗口逻辑。

---

### 2.21 `beta/ui_beta/game_lens_beta_window.py`

实验版入口窗口。

**作用**
- 通过 `beta/ui_beta/game_lens_beta_window.ui` 构建界面
- 显示标题为 `GameLens_beta` 的独立窗口
- 提供“选取游戏窗口”与“返回经典模式”按钮
- 将“返回经典模式”动作通过信号回传给主窗口
- 在 beta 模式下触发窗口选择后立即保存调试截图到 `beta/temp.jpg`
- `beta/` 下预留 `core_beta`、`memory_beta`、`ui_beta` 三个子目录，用于隔离实验版本代码

---

### 2.22 `beta/ui_beta/game_lens_beta_window.ui`

实验版窗口的 Qt Designer 界面文件。

**作用**
- 定义 `GameLens_beta` 的窗口标题、尺寸和按钮布局
- 由 `GameLensBetaWindow` 通过 `uic.loadUi()` 加载

---

### 2.23 `beta/ui_beta/window_selection_dialog.py`

实验版窗口选择对话框。

**作用**
- 枚举可见顶层窗口
- 展示窗口标题与类名
- 在用户确认后返回选中的窗口信息

---

### 2.24 `beta/core_beta/window_capture.py`

实验版窗口枚举与截图模块。

**作用**
- 枚举系统可见顶层窗口
- 过滤系统窗口与自身窗口
- 将选中的窗口截图保存为 JPEG

---

### 2.25 `beta/memory_beta/window_selection_state.py`

实验版窗口选择状态模块。

**作用**
- 暂存当前选择的游戏窗口信息
- 供 beta 窗口与后续截图逻辑共享

---

### 2.26 `config/config.txt`

运行配置文件。

**作用**
- 存放 API Key 和运行参数
- 被 `app_config.py` 读取

**当前配置项**
- `deepseek_api_key`
- `enable_ocr_preprocess`
- `top_proximity_threshold`
- `memory_window_size`
- `auto_recognition_interval_ms`

**说明**
- 配置采用 `[app]` 段管理，便于统一维护。

---

### 2.24 `data/game_lens.db`

SQLite 数据文件。

**作用**
- 存储游戏、游戏简介、人物、对话、摘要等持久化数据

**说明**
- 数据库文件由 `GameDatabase` 自动创建和维护，不需要手动建表。

---

## 3. 核心业务流程

1. `main.py` 启动程序；
2. `MainWindow` 加载主界面；
3. 用户通过 `screen_region_selector.py` 框选区域；
4. `ocr_engine.py` 完成识别；
5. `translator.py` 调用 DeepSeek 翻译；
6. `conversation_memory.py` 维护历史和摘要；
7. `database.py` 落库；
8. 各管理窗口负责后续维护；
9. 如需查看游戏背景信息，可通过 `game_intro_window.py` 生成或编辑游戏简介。

---

## 4. 备注

- 该项目的 GUI 主体是 PyQt6。
- OCR 依赖 PaddleOCR。
- 翻译、摘要与游戏简介生成依赖 DeepSeek API。
- 数据持久化基于 SQLite。
- 框选区域当前只保存在内存中，重启后不会保留。
- 当前实现对多显示器和高 DPI 环境做了适配。
