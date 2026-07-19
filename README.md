# 文献调研与报告助手 Review Writer

利用"Vibe Coding"做的第一个应用，略显粗糙。

当前版本：`0.7.0`

<img width="1236" height="864" alt="image" src="https://github.com/user-attachments/assets/cd21df49-bc25-4fa3-afea-66be8232752b" />


## 为什么做这个应用

研究生牛马经常要调研一些文献，用于申报项目之类的，今天调研这个，明天调研那个，每一次都要：
- 1.理解老板的想法；
- 2.到数据库检索文献；
- 3.阅读文献，写调研报告;

......

感觉太麻烦了，就利用AI Agent做了一个应用，能够：

- 1.根据用户输入的内容生成调研计划；
- 2.根据调研计划拆解或生成检索关键词；
- 3.链接到数据库，查找文献；
- 4.总结归纳文献，提取有用信息；
- 5.写成报告。
目前做出来的效果勉强能用，还算比较粗糙。

## 主要功能

### 1. 调研需求与计划

- 记录主题、目标、核心问题、年份范围、交付形式和范围条件。
- 使用本地规则或大模型 Agent 生成计划。
- 支持预览、编辑、保存草稿、确认计划和恢复既有项目。

### 2. 综述协议

- 支持普通调研、快速综述和系统综述。
- 保存纳入标准、排除标准、逐篇筛选决定、排除理由和质量评分。
- 根据逐篇记录计算 PRISMA 台账。

### 3. 关键词与检索式

- 生成可编辑的关键词树、宽检索式和精检索式。
- 保存 OpenAlex、Crossref、PubMed、Web of Science、CNKI 等数据库版本的检索式。
- 要求用户确认检索策略后再进入检索阶段。

### 4. 文献检索与全文

- 当前可执行 OpenAlex、Crossref、Zotero 和 IMA 检索。
- 使用 DOI 优先去重；缺少 DOI 时按题名和第一作者匹配。
- 支持关联 PDF、HTML、TXT、Markdown 和补充材料。
- 允许用户确认后小批量获取开放全文或机构授权全文。
- 校验 PDF 签名、文件大小、页数、文本层和题名匹配情况。

### 5. 结构化精读

- 提取研究问题、研究设计、样本或数据、方法、主要发现和局限性。
- 为证据块保存稳定 ID、页码、章节、图表和文档资产定位。
- 区分全文证据、摘要证据、知识库片段和元数据。

### 6. 报告与独立核验

- 提供学术综述、课题申报和行业调研模板。
- 使用本地证据综合或大模型 Agent 生成总结报告。
- 通过独立任务检查引用、DOI 格式、证据绑定和证据等级。
- 支持 Markdown、DOCX、PDF 和 RIS 导出；项目会生成 BibTeX 文件。
- 报告正文或论断台账改变后，旧核验结果会过期。

### 7. 任务队列与跨项目检索

- 使用 SQLite 保存检索、全文、OCR、精读、报告和核验任务。
- 支持日志、取消、失败重试和检查点恢复。
- 可在本机多个项目中搜索论文、证据块和精读笔记。

## 安装版使用方法

### 系统要求

- Windows 10 或 Windows 11，64 位。
- 基础工作流不要求安装 Python、uv 或 Node.js。
- 安装程序按当前 Windows 用户安装，不需要管理员权限。

### 安装步骤

1. 在 GitHub 仓库的 Releases 页面下载 `ReviewWriter-Setup-0.7.0.exe` 和 `SHA256SUMS.txt`。
2. 在下载目录打开 PowerShell，计算安装包哈希：

   ```powershell
   Get-FileHash .\ReviewWriter-Setup-0.7.0.exe -Algorithm SHA256
   ```

3. 确认结果与 `SHA256SUMS.txt` 一致，再运行安装程序。
4. 从开始菜单打开“文献调研与报告助手”。安装时也可以选择创建桌面快捷方式。

默认安装目录：

```text
%LOCALAPPDATA%\Programs\ReviewWriter
```

当前安装包没有代码签名证书。Windows SmartScreen 可能显示“未知发布者”。哈希不一致或文件来源不明时，不要运行安装包。

### 第一次使用

1. 点击“新建调研”，填写主题、目标和核心问题。
2. 选择“本地规则”生成计划。该模式不需要 API Key。
3. 检查并确认计划，然后按左侧 `01` 到 `08` 的阶段执行。
4. 需要模型能力时，在“设置 > 大模型设置”中填写服务商、模型和 API Key。
5. 应用在每次外部模型调用前显示发送内容和费用估算，请检查后再确认。

已有项目可以通过“打开已有项目”恢复。所选文件夹必须包含 `project.json` 和 `research_plan.md`。

## 可选组件

| 组件 | 用途 | 缺失时的影响 |
| --- | --- | --- |
| Edge 或 Chrome | 把报告打印为 PDF | Markdown、DOCX 和 RIS 仍可使用 |
| Node.js 22+ | 运行机构全文下载脚本 | 本地文件关联和公开元数据检索不受影响 |
| 已登录的 Chrome 与 CDP 代理 | 使用本人已有的机构授权会话 | 应用不会绕过登录、验证码或出版商检查 |
| Zotero Desktop | 通过本地 API 检索文献和附件 | OpenAlex、Crossref 和 IMA 仍可使用 |
| `ocrmypdf` | 处理扫描版 PDF | 扫描件会保留“需 OCR/人工补充”状态 |
| 大模型 API Key 或本地 Ollama | Agent 计划、精读、报告和语义核验 | 本地规则、确定性核验和项目管理仍可使用 |
| IMA OpenAPI 凭据 | 检索用户可见的 IMA 知识库 | 其他检索源不受影响 |

Zotero 集成直接调用 Zotero Desktop 本地 API。请先启动 Zotero，并在 Zotero 设置中启用本地 API。

机构全文功能只使用合法开放来源，或用户本人已经登录的授权浏览器会话。遇到 CAS、CARSI、验证码、二维码、短信验证、Cloudflare 或出版商机器人检查时，下载任务会停止并等待用户处理。

## 数据保存位置

安装版把配置和项目数据放在用户可写目录，不会写入安装目录。

| 内容 | 默认位置 |
| --- | --- |
| 设置 | `%LOCALAPPDATA%\ReviewWriter\settings.json` |
| DPAPI 加密凭据 | `%LOCALAPPDATA%\ReviewWriter\secrets.json` |
| 健康检查、模型目录缓存和跨项目索引 | `%LOCALAPPDATA%\ReviewWriter\` |
| 崩溃日志 | `%LOCALAPPDATA%\ReviewWriter\logs\crash.log` |
| 调研项目 | 当前用户“文档”目录下的 `Review Writer\Projects\` |

卸载程序不会删除调研项目和用户配置。确认不再需要后，用户可以手动删除这些目录。

源码运行时，应用继续使用仓库内的 `.local/` 和 `outputs/`。测试或便携部署可以设置以下环境变量：

```text
REVIEW_WRITER_DATA_DIR
REVIEW_WRITER_PROJECTS_DIR
```

## 项目产物

每个调研项目会逐步生成以下文件。实际文件数量取决于使用的工作流。

```text
project.json
research_plan.md
review/protocol.json
review/screening.json
review/quality_assessments.json
review/prisma.json
search/keyword_tree.md
search/search_strategy.json
search/papers.json
search/references.bib
fulltext/
reading_notes/
report/literature_summary.md
report/claim_ledger.json
audit/verification_report.md
diagnostics/tasks.sqlite3
exports/
```

应用为导入文档保存 SHA-256、资产 ID 和定位信息。扫描版、题名不匹配或解析失败的材料不会自动升级为全文证据。

## 使用注意事项

- 自动核验不能代替人工学术审阅，也不能保证报告符合 PRISMA、Cochrane 或目标期刊规范。
- 系统综述模式提供协议、筛选和 PRISMA 记录工具。研究者仍需设计检索策略并评估偏倚风险。
- 核心论文评分是可解释的启发式排序，不代表期刊质量或权威证据等级。
- 本地 DOI 核验只检查格式。确认 DOI 是否真实注册需要联网查询权威元数据源。
- Token 和费用属于预估值，实际账单以模型服务商为准。
- Windows DPAPI 通常只能由保存密钥的 Windows 用户解密。迁移到另一台电脑后，请重新填写 API Key。
- 调研项目和下载的全文没有加密。请保护机构授权材料和敏感研究数据。
- 应用不读取或保存机构密码、Cookie、验证码、二维码或 OTP。
- 请遵守数据库、出版商和所在机构的授权条款。不要把 `outputs/` 或含受限全文的项目目录上传到公开仓库。

## 常见问题

### 安装包触发 SmartScreen

当前发布物没有代码签名。请从项目 Release 下载，并先对照 `SHA256SUMS.txt`。无法确认来源时，删除安装包。

### PDF 导出不可用

在“设置 > 健康检查”中查看浏览器后端。安装 Edge 或 Chrome 后重试。Markdown 和 DOCX 导出不依赖浏览器打印。

### 扫描版 PDF 没有正文

安装 `ocrmypdf`，或先用可信 OCR 工具生成带文本层的 PDF，再重新关联文件。OCR 完成后，应用仍会执行 PDF 校验。

### Zotero 无法连接

启动 Zotero Desktop，启用本地 API，并确认 `http://127.0.0.1:23119` 没有被防火墙拦截。

### 应用启动后退出

查看：

```text
%LOCALAPPDATA%\ReviewWriter\logs\crash.log
```

提交问题时请移除 API Key、机构信息和受限全文内容。

## 从源码运行

开发环境需要 Python 3.14 或更高版本，并建议使用 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --dev
uv run review-writer
```

也可以运行入口文件：

```powershell
uv run python main.py
```

## 测试

```powershell
uv run python -m unittest discover -s tests -v
```

测试使用本地临时文件和模拟响应，不调用真实模型、出版商、院校资源或用户知识库。

## 构建 Windows 安装包

构建脚本会运行测试、生成 PyInstaller 目录版、执行冻结应用烟雾测试，再调用 Inno Setup 6 编译安装程序。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建产物：

```text
dist/ReviewWriter/ReviewWriter.exe
dist/installer/ReviewWriter-Setup-0.7.0.exe
dist/installer/SHA256SUMS.txt
```

开发依赖由 `pyproject.toml` 和 `uv.lock` 固定。构建安装器还需要 Inno Setup 6；使用 `-SkipInstaller` 可以只生成目录版。

## 代码结构

| 路径 | 内容 |
| --- | --- |
| `main.py` | 应用入口、崩溃日志和冻结应用烟雾测试 |
| `review_writer/ui.py` | 主窗口、导航和需求计划界面 |
| `review_writer/workflow_view.py` | 综述、检索、精读、报告和核验工作台 |
| `review_writer/workflow_store.py` | 项目 schema、迁移和工作流状态 |
| `review_writer/search_engine.py` | 多源检索与去重 |
| `review_writer/fulltext.py` | 全文获取和 PDF 校验 |
| `review_writer/reader.py` | 结构化精读与证据块 |
| `review_writer/reporting.py` | 总结、论断台账和核验 |
| `review_writer/task_queue.py` | SQLite 持久化任务队列 |
| `tests/` | 单元测试和界面回归测试 |
| `packaging/` | PyInstaller 与 Inno Setup 配置 |

## 第三方组件

安装包携带 `nature-downloader` 的运行脚本，用于用户确认后的合法全文获取。该组件采用 MIT 许可证，许可证原文见 [`vendor/nature-downloader/LICENSE`](vendor/nature-downloader/LICENSE)。

项目级许可证将在首次 GitHub 发布前由仓库所有者确认。
