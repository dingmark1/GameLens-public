# GameLens 项目结构说明

## 1. 项目概览

GameLens 是一个基于 Python + PyQt6 的游戏文本识别/翻译工具，核心流程是：

1. 框选游戏画面区域；
2. 对截图做 OCR 识别；
3. 将结构化结果交给 DeepSeek 翻译；
4. 把对话、人物、摘要等信息写入本地 SQLite；
5. 通过独立管理窗口维护人物 / 对话 / 摘要数据。

---

## 2. 目录与文件说明

### 2.1 `src/main.py`

程序入口。

**作用**
- 创建 `QApplication`
- 启动前清空内存中的框选区域缓存
- 创建并显示主窗口 `MainWindow`
- 进入 Qt 事件循环

**关键功能**
- `main()`：应用启动流程

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

---

### 2.5 `src/memory/database.py`

SQLite 持久化层。

**作用**
- 管理游戏、人物、对话、摘要数据
- 自动建表、建索引、做必要迁移
- 为 UI 与记忆模块提供统一数据接口

**核心数据表**
- `games`
- `characters`
- `dialogues`
- `summaries`

**关键功能**
- `add_game()` / `delete_game()` / `list_games()`
- `add_character()` / `update_character()` / `delete_character()`
- `add_dialogue()` / `update_dialogue()` / `delete_dialogue()` / `clear_dialogues()`
- `add_summary()` / `update_summary()` / `delete_summary()`
- `get_all_dialogues_with_game_name()`
- `get_all_characters_with_game_name()`
- `get_all_summaries_with_game_name()`
- `get_latest_summary()` / `get_latest_summary_record()`
- `get_dialogues_by_game_range()`

**额外能力**
- 对话表迁移
- 人物删除时级联处理对话引用
- 线程锁保护 SQLite 访问

---

### 2.6 `src/memory/conversation_memory.py`

对话记忆与摘要生成模块。

**作用**
- 维护全局对话窗口记忆
- 去重 OCR 对话结果
- 自动触发前情回顾生成
- 基于最近对话推断人物补充信息

**关键功能**
- `get_conversation_memory()`
- `append_conversation_record()`
- `append_ocr_dialog_result()`
- `get_recent_conversation_records()`
- `get_conversation_summary()` / `set_conversation_summary()`
- `clear_conversation_memory()` / `clear_conversation_summary()`
- `is_duplicate_ocr_dialog_result()`

**摘要相关**
- `_trigger_summary_generation_if_needed()`
- `_generate_summary_task()`
- `_call_summary_api()`
- `_apply_summary_addition()`

**人物信息相关**
- `_call_character_relation_inference_api()`
- `_parse_addition_updates()`
- `_merge_extra_info()`

---

### 2.7 `src/ui/main_window.py`

主窗口与业务调度中心。

**作用**
- 承载主界面 UI
- 协调选区、OCR、翻译、存储、展示
- 管理自动识别与后台线程
- 打开人物 / 对话 / 摘要管理窗口

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

**重要方法**
- `MainWindow._start_screen_region_selection()`
- `MainWindow._start_ocr_recognition()`
- `MainWindow._perform_single_recognition()`
- `MainWindow._start_auto_recognition()`
- `MainWindow._stop_auto_recognition()`
- `MainWindow._show_translation_overlay()`
- `MainWindow._process_pending_dialogue_storage()`

---

### 2.8 `src/ui/screen_region_selector.py`

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

---

### 2.9 `src/ui/character_manager_window.py`

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

---

### 2.10 `src/ui/dialogue_manager_window.py`

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

---

### 2.11 `src/ui/summary_manager_window.py`

摘要管理窗口。

**作用**
- 展示摘要列表
- 修改摘要内容
- 删除摘要记录

**关键功能**
- `refresh_summaries()`
- `_on_edit_clicked()`
- `_on_delete_clicked()`

---

### 2.12 `src/ui/edit_character_dialog.py`

人物编辑对话框。

**作用**
- 修改选中人物的可编辑字段
- 保存后通知列表刷新

**关键功能**
- `EditCharacterDialog._on_accept()`

---

### 2.13 `src/ui/edit_dialogue_dialog.py`

对话编辑对话框。

**作用**
- 修改选中对话内容
- 校验人物与游戏关系

**关键功能**
- `EditDialogueDialog._on_accept()`

---

### 2.14 `src/ui/edit_summary_dialog.py`

摘要编辑对话框。

**作用**
- 修改选中摘要内容
- 展示所属游戏和覆盖范围

**关键功能**
- `EditSummaryDialog._on_accept()`

---

### 2.15 `src/core/__init__.py`

核心模块导出入口。

**作用**
- 统一导出 OCR、记忆、数据库相关常用对象/函数

---

### 2.16 `src/memory/__init__.py`

记忆模块导出入口。

**作用**
- 暴露对话记忆、摘要、数据库能力给上层调用

---

### 2.17 `src/ui/__init__.py`

UI 模块导出入口。

**作用**
- UI 包初始化文件
- 便于模块化导入

---

### 2.18 `src/ui/main_window.ui`

Qt Designer 界面文件。

**作用**
- 定义主窗口布局、按钮、下拉框等控件
- 由 `MainWindow` 通过 `uic.loadUi()` 加载

---

### 2.19 `config/config.txt`

运行配置文件。

**作用**
- 存放 API Key 和运行参数
- 被 `app_config.py` 读取

---

### 2.20 `data/game_lens.db`

SQLite 数据文件。

**作用**
- 存储游戏、人物、对话、摘要等持久化数据

---

## 3. 核心业务流程

1. `main.py` 启动程序；
2. `MainWindow` 加载主界面；
3. 用户通过 `screen_region_selector.py` 框选区域；
4. `ocr_engine.py` 完成识别；
5. `translator.py` 调用 DeepSeek 翻译；
6. `conversation_memory.py` 维护历史和摘要；
7. `database.py` 落库；
8. 各管理窗口负责后续维护。

---

## 4. 备注

- 该项目的 GUI 主体是 PyQt6。
- OCR 依赖 PaddleOCR。
- 翻译与摘要依赖 DeepSeek API。
- 数据持久化基于 SQLite。
