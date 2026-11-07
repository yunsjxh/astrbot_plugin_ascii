# 上游 ASCII-generator 的连接与配置方法

本文件说明 `astrbot_plugin_ascii` 如何与 **https://github.com/vietnh1009/ASCII-generator** 对接。这是安装本插件时**唯一必须额外准备**的外部依赖。

---

## 0. 先说清楚「连接」是什么

插件不通过 HTTP、WebSocket、消息队列或 Python import 调用上游；它启动一个**本地子进程**：

```
AstrBot (main.py)
  │  asyncio.create_subprocess_exec(
  │      python_path, script,
  │      --input <图片> --output <结果> --num_cols N
  │      --language english --background black|white,
  │      cwd = generator_dir)
  ▼
上游 img2img.py / img2img_color.py
  │  读取 generator_dir/fonts/DejaVuSansMono-Bold.ttf（相对路径！）
  ▼
写出 <结果>.jpg  →  插件读字节  →  Image.fromBytes()  →  aiocqhttp base64:// → NapCat
```

由此产生三条硬约束：

1. **同机同文件系统**：`generator_dir` 必须是 AstrBot 进程自己能读到的本地目录。不能填 URL、不能指向另一台机器，也不能只挂在 NapCat 容器里。
2. **工作目录**：上游用 `fonts/DejaVuSansMono-Bold.ttf` 这类相对路径加载字体，所以子进程 `cwd` 必须正好是 `generator_dir`。插件已经处理了这一点，你只要保证目录结构完整。
3. **解释器**：`python_path` 指向的解释器必须能 `import numpy, cv2, PIL`。它与 AstrBot 用哪个 Python 无关，是独立子进程。

NapCat 容器**不需要**安装上游：插件把结果读成字节后用 base64 发送，两个容器之间不共享文件路径。

---

## 1. 上游仓库信息

| 项 | 值 |
| --- | --- |
| 地址 | https://github.com/vietnh1009/ASCII-generator |
| 许可 | MIT（Copyright (c) 2018 Viet Nguyen） |
| 本项目验证的提交 | `d69701624e05573ce1670caa0fa27b4b192d7b13`（2024-11-22） |
| 插件实际用到的文件 | `img2img.py`、`img2img_color.py`、`utils.py`、`alphabets.py`、`fonts/DejaVuSansMono-Bold.ttf` |
| 未用到 | `img2txt.py`、`video2video*.py`、`demo/`、其它语言字体 |

`demo/` 目录约占 533 MB，全是示例 GIF / 图片，运行时完全用不到。所以推荐**稀疏检出**，装完约 800 KB：

```bash
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/vietnh1009/ASCII-generator.git /opt/ASCII-generator

git -C /opt/ASCII-generator sparse-checkout set --no-cone \
  '/*.py' '/README.md' '/LICENSE' '/fonts/DejaVuSansMono-Bold.ttf'
```

也可以直接完整 clone（约 533 MB），功能一样，只是占空间。若你已有该仓库，跳过这一步，直接把 `generator_dir` 指过去即可。

> 仓库里的 `simsun.ttc`（中文字体）与 `arial-unicode.ttf` 属于商业字体，本项目**未安装也不分发**。插件固定使用 `--language english`，因此只需 `DejaVuSansMono-Bold.ttf`。

---

## 2. Pillow >= 10 兼容补丁（必做）

上游代码使用 `PIL.ImageFont.FreeTypeFont.getsize()`，该方法已在 Pillow 10 移除。在 Pillow 12 上实测：

```
python 3.12.3 / Pillow 12.0.0 / numpy 2.2.6 / opencv 4.12.0
getsize present: False
```

不打补丁会出现 `AttributeError: 'FreeTypeFont' object has no attribute 'getsize'`，转换全部失败。本项目提供 [patches/pillow12-getsize.patch](../patches/pillow12-getsize.patch)，把 5 个文件里共 9 处 `getsize(x)` 改为等价的 `getbbox(x)` 宽高计算：

```python
_l, _t, _r, _b = font.getbbox("A")
char_width, char_height = _r - _l, _b - _t
```

应用方式：

```bash
# 在本仓库根目录执行：git -C 会切换工作目录，patch 路径必须写成绝对路径
PATCH="$(pwd)/patches/pillow12-getsize.patch"
git -C /opt/ASCII-generator apply "$PATCH"
```

或让安装脚本代劳（见第 6 节）。补丁基于上面列出的提交；若上游更新过，可能需要手工对照修改。

验证补丁已生效：

```bash
cd /opt/ASCII-generator && /opt/AstrBot/venv/bin/python img2img.py \
  --input /tmp/test.jpg --output /tmp/test_ascii.jpg \
  --num_cols 100 --language english --background black
```

能生成图片即表示上游链路通了。

> `img2txt.py` 里有一个 `\|` 引起的 `SyntaxWarning`，无害，本项目未修改它。视频脚本的 XVID 编码丢帧问题同样不在本插件范围内。

---

## 3. 准备 Python 解释器

上游脚本需要 `numpy`、`opencv`（推荐 `opencv-python-headless`）、`Pillow`。**最省事的做法是直接复用 AstrBot 自己的虚拟环境**，通常它已经装齐了这些依赖：

```bash
/opt/AstrBot/venv/bin/python -c "import numpy, cv2, PIL; print(PIL.__version__)"
```

如果要用独立环境：

```bash
python3 -m venv /opt/ascii-venv
/opt/ascii-venv/bin/pip install numpy opencv-python-headless Pillow
```

自检（三项都应通过）：

```bash
/opt/AstrBot/venv/bin/python -c "import numpy, cv2, PIL; from PIL import ImageFont; \
print('numpy', numpy.__version__, 'cv2', cv2.__version__, 'Pillow', PIL.__version__); \
print('getsize removed as expected:', not hasattr(ImageFont.FreeTypeFont, 'getsize'))"
```

把 `python_path` 填成这个解释器的**绝对路径**。

---

## 4. 把插件「连」上去：两个配置项

配置文件位置：`/opt/AstrBot/data/config/astrbot_plugin_ascii_config.json`（AstrBot 自动生成，可能带 UTF-8 BOM）。

| 键 | 填什么 | 校验方法 |
| --- | --- | --- |
| `generator_dir` | 上游仓库根目录，例如 `/opt/ASCII-generator` | `ls $generator_dir/img2img.py $generator_dir/fonts/DejaVuSansMono-Bold.ttf` 都存在 |
| `python_path` | 第 3 节那个解释器，例如 `/opt/AstrBot/venv/bin/python` | 上一步自检命令通过 |

### 方式 A：WebUI（推荐）

1. 打开 AstrBot WebUI → **插件管理 / 已安装**；
2. 找到 **ASCII 字符画**，打开配置页（表单由 `_conf_schema.json` 自动生成）；
3. 填写 `generator_dir` 与 `python_path`，按需调整 `num_cols`、`color` 等；
4. **保存** → 再点一次 **「重载」插件**。

> 刚克隆/上传插件、或改过 `main.py` / `_conf_schema.json` 之后，都必须重载一次，运行中的 AstrBot 进程才会重新扫描插件目录并加载新配置。仅改文件不重载，插件列表和配置项都不会变化。

### 方式 B：直接写配置文件

编辑 `/opt/AstrBot/data/config/astrbot_plugin_ascii_config.json`（注意 BOM，建议用 `utf-8-sig` 或用 WebUI 保存一次）：

```json
{
  "num_cols": 100,
  "recent_image_ttl": 60,
  "fix_aspect_ratio": true,
  "keep_original_size": false,
  "color": false,
  "white_background": false,
  "timeout": 30,
  "generator_dir": "/opt/ASCII-generator",
  "python_path": "/opt/AstrBot/venv/bin/python"
}
```

保存后同样需要重载插件。

> **配置优先级**：文件里已保存的显式值会覆盖代码默认值。升级插件后若发现输出又变小变糊，先检查配置页里的 `keep_original_size` 是否还留着旧值 `true`。

---

## 5. Docker / 容器场景

| 部署形态 | 怎么做 |
| --- | --- |
| AstrBot 在宿主机、NapCat 在容器（本项目验证的形态） | 上游装在宿主机即可，NapCat 不需要它 |
| AstrBot 在容器内 | 上游必须**也在这个容器内**，或用 volume 把宿主机目录挂进同一容器的相同路径，再让 `generator_dir` 指向挂载点 |
| 上游只装在 NapCat 容器 | **不行**，子进程由 AstrBot 进程发起，看不到那个文件系统 |

容器里还要注意 `python_path` 必须是**容器内**的解释器路径。

---

## 6. 一键安装脚本

仓库自带 [scripts/install_generator.sh](../scripts/install_generator.sh)，只做稀疏克隆、（可选）打补丁、自检，**不删除任何已有文件**：

```bash
PYTHON_BIN=/opt/AstrBot/venv/bin/python \
  ./scripts/install_generator.sh /opt/ASCII-generator --apply-patch
```

省略 `--apply-patch` 则只克隆、不修改上游源码（适合你打算自己管理补丁的情况）。目标目录已存在时脚本会直接退出，不会覆盖。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 回复「转换脚本没找到」 | `generator_dir` 指错或没装上游 | 检查 `ls $generator_dir/img2img.py` |
| `AttributeError: ... getsize` | 没打 Pillow 补丁 | 见第 2 节应用 patch |
| `OSError: cannot open resource` / 字体报错 | 缺 `fonts/DejaVuSansMono-Bold.ttf`，或子进程 `cwd` 不对 | 稀疏检出时确认包含该字体；插件已固定 `cwd = generator_dir` |
| 回复「转换程序启动失败」 | `python_path` 不是可执行文件 / 路径错 | 用绝对路径，确认 `chmod +x` |
| 回复「转换失败了」 | 上游脚本异常，通常是依赖缺失或输入图损坏 | 用第 2 节的手动 CLI 命令复现，看 stderr |
| 回复「图片太大或转换超时了」 | 大图 + 高 `num_cols` | 调小 `num_cols` 或调大 `timeout`（下限 5 秒） |
| 机器人不回图 / 图发不出去 | 结果路径被跨容器隔离 | 插件已用 `Image.fromBytes()` + base64 发送；若仍失败检查 aiocqhttp 适配器日志 |
| 「没抓到 xxx 的头像」 | 服务器无法访问 `q1.qlogo.cn`，或 QQ 号无效 | `curl -I 'https://q1.qlogo.cn/g?b=qq&nk=10000&s=640'`；这是插件唯一需要公网的地方 |
| 配置改了没生效 / WebUI 看不到新配置项 | 没重载插件 | 保存后点「重载」；必要时 `journalctl -u astrbot --since '...'` 看加载日志 |
| 输出被压扁或字符糊成一片 | 比例 / 尺寸设置 | 保持 `fix_aspect_ratio=true`、`keep_original_size=false` |

---

## 8. 最小验证清单

```bash
# 1) 上游文件齐全
test -f /opt/ASCII-generator/img2img.py && \
test -f /opt/ASCII-generator/fonts/DejaVuSansMono-Bold.ttf && echo "generator OK"

# 2) 解释器依赖齐全
/opt/AstrBot/venv/bin/python -c "import numpy, cv2, PIL; print('deps OK')"

# 3) 手动跑一次上游 CLI（不经插件）
cd /opt/ASCII-generator && /opt/AstrBot/venv/bin/python img2img.py \
  --input /tmp/test.jpg --output /tmp/test_ascii.jpg \
  --num_cols 100 --language english --background black && echo "CLI OK"

# 4) 插件侧：WebUI 保存配置 → 重载 → QQ 发图片 + /ascii
```

四项都通过，`generator_dir` / `python_path` 这条「连接」就建立好了。
