# KindleTransfer — Kindle 智能传书助手 V0.2.1

一个 Windows 桌面端电子书传输工具。不依赖 Amazon 账号、Send to Kindle 或任何云服务，直接通过 USB 把电子书传到 Kindle。

插上 Kindle 即自动识别，不需要自己找盘符，也不需要知道书该放进哪个目录。

## 当前支持

- **平台**：Windows 10 / Windows 11
- **设备**：
  - `kindle_oasis_3` — Kindle Oasis 3（10th Generation, 2019），**已真机验收**
  - `generic_kindle` — 通用 Kindle，保守配置（未在真机逐项验证）
- **转换后端**：calibre 的 `ebook-convert`（可选；不装也能用直接传输的格式）

## 自动设备识别

设备识别**不依赖盘符**，而是对每个卷做多特征评分：

| 特征 | 加分 |
|------|------|
| 卷标含 `Kindle` | +3 |
| 卷标含 `Amazon` | +2 |
| 存在 `documents` 文件夹 | +2 |
| 存在 Kindle 特征文件夹（`.active_content_sandbox` / `fonts` / `audible` 等） | +1 ~ +2 |
| 可移动存储设备 | +1 |
| FAT / FAT32 文件系统 | +1 |
| 容量落在 2–64 GB | +1 |

**强特征门禁**：必须满足「卷标含 Kindle/Amazon」**或**「可移动设备」之一，否则即使分数够也不判为 Kindle。这条规则防止内置 NTFS 硬盘仅因为恰好有 `documents` 文件夹而被误判。

评分 ≥ 7 视为高可信。识别过程**只读**，不会创建或修改设备上的任何文件。

### 热插拔

`DeviceManager` 每 2 秒轮询一次卷列表（仅读取 Win32 卷信息，开销极低）。插入或拔出后界面会自动更新。

## 多设备安全规则（V0.2.1）

| 情况 | 行为 |
|------|------|
| 0 个候选 | 显示未检测到设备 |
| 1 个高可信候选 | 自动选中 |
| 1 个低可信候选 | 显示但**不**允许传输，需用户确认 |
| ≥ 2 个候选 | **绝不自动选择**，弹出选择器由用户指定 |
| 已选设备被拔掉、另一台仍在 | **绝不静默切换**，清空选择并要求重新确认 |

即：不会因为某个设备评分高 1～2 分就自动向它写文件。

### 传输前二次校验

点击「智能传送」时会重新验证：

1. 根目录仍存在
2. `documents` 仍存在且可写
3. **重新读取该盘符的卷序列号，与用户选定的目标比对**

因此「原 E: 的 Kindle 被拔掉、另一个 U 盘占用了 E:」会被识别出来并阻止传输，不会仅因为 `E:\documents` 存在就继续写。

## 设备指纹与型号映射

设备身份使用 **卷序列号**（`GetVolumeInformationW` 返回），**不使用盘符**。所以盘符从 `E:` 变成 `F:` 不会丢失配置。

首次连接且无法确定型号时，会询问一次：

- Kindle Oasis 3
- Generic Kindle / 暂不确定

选择后保存 `设备序列号 → profile` 映射，之后同一设备再连接会自动恢复，不再询问。可通过菜单「工具 → 更改当前 Kindle 型号」随时修改。

## 格式处理

设备能力由 `config/devices.json` 数据驱动，新增设备只需加配置，无需改业务代码。

### Kindle Oasis 3

| 格式 | 动作 |
|------|------|
| `.azw3` `.azw` `.mobi` `.prc` `.txt` `.pdf` | 直接传输 |
| `.epub` `.docx` `.html` `.htm` `.rtf` | 转换为 `.azw3`（需 ebook-convert） |
| 其他 | 不支持 |

### Generic Kindle（严格白名单）

| 格式 | 动作 |
|------|------|
| `.azw3` `.mobi` `.pdf` `.txt` | 直接传输 |
| `.epub` | 转换为 `.azw3`（需 ebook-convert） |
| 其他 | 不支持 |

> Generic 配置刻意保守：`.azw` / `.prc` 未在真机确认，因此暂不列入。判定逻辑严格为「在白名单 → direct；在转换表 → convert；否则 → unsupported」，不存在 `else → direct` 的兜底行为。

## 安装

### 1. Python 依赖

```bash
pip install -r requirements.txt
```

需要 Python 3.9+（开发环境为 3.14，另在 3.11/3.12/3.13 上可用）。

### 2. calibre（可选）

格式转换依赖 calibre 提供的 `ebook-convert`。

1. 从 [calibre 官网](https://calibre-ebook.com/download) 下载安装。
2. 程序会自动查找（顺序：已保存路径 → PATH → 常见安装目录）。
3. 找不到时可用菜单「工具 → 选择 ebook-convert」手动指定，路径会被记住。

不装 calibre 时：直接传输的格式照常可用，需要转换的（如 EPUB）会显示为无法转换。

## 运行

**方式一：双击启动器**

```
KindleTransfer.bat
```

出现菜单，按 `1` 启动程序，按 `2` 跑测试。也可带参数跳过菜单：

```
KindleTransfer.bat app     # 直接启动
KindleTransfer.bat test    # 直接跑测试
```

启动器会自动定位 Python 解释器（项目 `.venv` → 系统安装 → uv 托管 → PATH），并会识别出 PATH 上那种 0 字节的微软商店占位符。

**方式二：命令行**

```bash
python main.py
```

## 使用流程

1. 连接 Kindle —— 程序自动识别设备并选好目标目录。
2. 点「选择电子书」，或直接把文件拖进列表。
3. 需要去掉某些文件时：
   - 点「移除选中」按钮
   - 或在列表里右键 → 「移除选中」/「清空列表」
   - 或选中后按 `Delete` 键
   - 支持 `Ctrl` / `Shift` 多选，可一次移除多行
4. 点「智能传送」。

> **列表的「移除」只针对待传列表**，表示「这个不传了」。磁盘上的原文件**不会被删除**，随时可以重新添加。
>
> 传送进行中列表会被锁定，避免行号错位——等传送结束再修改。

## 运行测试

```bash
pytest
# 或
python -m pytest tests/ -q
```

当前：**173 passed, 1 skipped, 0 failed**。

跳过的那 1 项是需要真实 Kindle 连接的集成测试。

## 系统检查

菜单「工具 → 系统检查」可查看：

- Python 版本与操作系统
- Kindle 识别结果、识别依据、根目录、`documents` 是否存在且可写、剩余空间
- `ebook-convert` 是否找到且可运行

## DRM 声明

**本程序不提供、也不会实现任何 DRM 移除功能。**

如果 calibre/ebook-convert 因 DRM 或加密保护无法转换，程序会停止处理该文件并提示：

> 该文件可能受到 DRM 或其他加密保护，无法转换。本程序不会移除 DRM。

请勿使用本程序破解、移除或规避 DRM。

## 真机验收（Kindle Oasis 3）

1. USB 连接 Kindle Oasis 3。
2. 启动程序 —— 应自动识别并显示设备型号、根目录、`documents`、可用空间。
3. 拖入测试文件（`.epub` / `.pdf` / `.azw3`，确保无 DRM、无版权问题）。
4. 确认列表显示：EPUB → 转换 AZW3；PDF → 直接传输；AZW3 → 直接传输。
5. 点击「智能传送」，在覆盖策略中选择「否（跳过已有文件）」。
6. 三个文件状态变为「成功」，`documents` 中可见。
7. 安全弹出 Kindle，在设备上确认：
   - PDF、原 AZW3、转换得到的 AZW3 均可正常打开
   - 中文显示正常、目录正常、翻页正常

已实测通过：EPUB → AZW3 转换后传入 Oasis 3，中文排版与渲染正常。

## 项目结构

```
KindleTransfer/
├── main.py                        # 程序入口（含日志初始化）
├── KindleTransfer.bat             # 双击启动器（菜单：启动 / 测试）
├── requirements.txt
├── README.md
├── app/
│   ├── ui/
│   │   └── main_window.py         # PySide6 GUI + 传输工作线程
│   ├── devices/
│   │   ├── windows_detector.py    # Win32 卷枚举（ctypes，无额外依赖）
│   │   ├── device_matcher.py      # 多特征评分识别 + 指纹映射持久化
│   │   ├── device_manager.py      # 轮询、热插拔、多设备安全策略
│   │   ├── profiles.py            # 设备配置加载
│   │   └── detector.py            # Kindle 根目录校验
│   ├── books/
│   │   └── analyzer.py            # 文件格式分析与动作决策
│   ├── converter/
│   │   └── calibre_converter.py   # ebook-convert 封装（可取消、QSettings 持久化）
│   └── transfer/
│       └── usb_transfer.py        # 传输、校验、空间检查
├── config/
│   └── devices.json               # 设备能力配置
└── tests/
    ├── test_device_detection.py       # 评分与识别
    ├── test_device_manager_polling.py # 多设备/断开/指纹 安全规则
    ├── test_file_list_management.py   # 列表移除/清空（含"不删原文件"保证）
    ├── test_profiles.py
    ├── test_analyzer.py
    ├── test_transfer.py
    ├── test_calibre_converter.py
    ├── test_settings.py
    ├── test_pipeline.py               # 线程生命周期与安全退出
    └── integration/
        └── test_real_calibre.py       # 真实 calibre 转换
```

## 已知限制

- 仅 Kindle Oasis 3 经过真机逐项验收；`generic_kindle` 为保守配置，未经真机验证。
- 仅提供两个设备配置，未包含 Paperwhite / Scribe / Kobo。
- **不做 PDF → AZW3 转换**（PDF 直接传输；重排效果取决于 Kindle 自身）。
- 传输校验仅比较文件大小，未使用 SHA-256。
- 不做 PDF OCR。
- 无 Wi-Fi / 局域网传输，无 Amazon 账号或 API 依赖。

## 后续计划

- 更多机型的真机验证与配置。
- SHA-256 校验。
- 转换进度显示。
- 配置文件热加载。

## 许可

尚未指定开源许可证。
