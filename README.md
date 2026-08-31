# GameLens

GameLens 是一款面向 Windows 的游戏文本识别与翻译工具。它可以框选屏幕中的游戏文本，通过 PaddleOCR 完成识别，再调用 DeepSeek API 进行翻译，并将人物、对话、摘要和游戏简介保存到本地 SQLite 数据库。

## 主要功能

- 框选游戏画面并识别文字
- 单次识别与定时自动识别
- DeepSeek 对话翻译
- 对话上下文记忆与自动摘要
- 游戏、人物、对话和摘要管理
- 自动生成并编辑游戏简介
- 支持多显示器和高 DPI 屏幕
- 提供实验性的游戏窗口自动解析模式

## 环境要求

- Windows 10 / 11
- Python 3.10（推荐）
- DeepSeek API Key

## 安装

在项目根目录打开 PowerShell，创建并激活虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装运行依赖：

```powershell
pip install PyQt6 paddlepaddle paddleocr opencv-python numpy Pillow mss requests langchain-classic pywin32
```

> PaddlePaddle 的安装方式可能因 Python 版本、CPU/GPU 和 CUDA 环境而不同。如安装失败，请参考 PaddlePaddle 官方安装说明选择合适的版本。

## 配置

编辑 `config/config.txt`：

```ini
[app]
deepseek_api_key = your_deepseek_api_key
enable_ocr_preprocess = true
top_proximity_threshold = 0.2
memory_window_size = 10
memory_window_multiplier = 3
auto_recognition_interval_ms = 2500
```

配置项说明：

| 配置项 | 说明 |
| --- | --- |
| `deepseek_api_key` | DeepSeek API Key |
| `enable_ocr_preprocess` | 是否在 OCR 前进行图像预处理 |
| `top_proximity_threshold` | OCR 文本位置判定阈值 |
| `memory_window_size` | 对话记忆窗口大小 |
| `memory_window_multiplier` | 摘要触发窗口倍数 |
| `auto_recognition_interval_ms` | 自动识别间隔，单位为毫秒 |

请妥善保管 API Key，不要将真实密钥提交到公开仓库。

## 运行

在项目根目录执行：

```powershell
python .\src\main.py
```

首次启动 OCR 时可能需要下载模型，请保持网络连接并耐心等待。

## 基本使用

1. 启动 GameLens。
2. 添加或选择当前游戏。
3. 选择 OCR 语言。
4. 点击框选按钮，选中游戏中的对话区域。
5. 使用单次识别，或开启自动识别。
6. 在翻译浮层中查看结果。
7. 通过管理窗口维护人物、对话、摘要和游戏简介。

主窗口还可以切换到 `GameLens_beta` 实验模式。该模式允许直接选择游戏窗口，并尝试自动解析对话区域。

## 项目结构

```text
GameLens/
├─ src/
│  ├─ main.py                 # 程序入口
│  ├─ core/                   # OCR、翻译、配置和简介生成
│  ├─ memory/                 # 对话记忆、摘要和数据库
│  └─ ui/                     # 主窗口、管理窗口和屏幕框选
├─ beta/
│  ├─ core_beta/              # 实验版识别与窗口解析
│  ├─ memory_beta/            # 实验版运行状态与摘要
│  └─ ui_beta/                # 实验版界面
├─ config/
│  └─ config.txt              # 本地运行配置
└─ data/
   └─ game_lens.db            # SQLite 数据库
```

数据库会由程序自动初始化，无需手动建表。屏幕框选区域仅保存在内存中，程序重启后需要重新选择。

## 技术栈

- Python
- PyQt6
- PaddleOCR / PaddlePaddle
- DeepSeek API
- SQLite

更详细的模块说明请参阅 [GameLens_项目结构说明.md](./GameLens_项目结构说明.md)。
